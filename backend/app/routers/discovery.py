"""発掘商品候補 API（Discovery Engine v1-1）。

- POST  /discovery/products         商品候補を登録（source_url 重複時は既存を再利用）
                                     auto_score=true で登録直後に自動スコアリング
- GET   /discovery/products         一覧（platform/status/category/min_score/sort で絞り込み）
- GET   /discovery/products/{id}    詳細取得
- PATCH /discovery/products/{id}    更新（渡されたフィールドのみ）
- POST  /discovery/products/{id}/score  保存済み商品を AI/ルールでスコアリング
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.discovery import (
    DiscoveredProductCreate,
    DiscoveredProductOut,
    DiscoveredProductUpdate,
    DiscoveryContactIntelligenceResult,
    DiscoveryJobOut,
    DiscoveryPlatformInfo,
    DiscoveryRunJobResponse,
    DiscoveryRunRequest,
)
from app.services import discovery_job_service, discovery_service
from app.services.discovery_adapters import is_live_fetch, platform_availability

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.get("/platforms", response_model=list[DiscoveryPlatformInfo])
def list_platforms() -> list[DiscoveryPlatformInfo]:
    """発掘元プラットフォームの対応状況（実取得対応 / 準備中）を返す。

    UI はこれを取得して選択肢・実行ボタンの活性を決める（backend の実態と一致させ、
    「準備中」と「実行可能」のハードコード二重管理を避ける）。読み取り専用・DB 非依存。
    """
    return platform_availability()


@router.post("/run", response_model=DiscoveryRunJobResponse)
def run_discovery(
    payload: DiscoveryRunRequest, db: Session = Depends(get_db)
) -> DiscoveryRunJobResponse:
    """発掘ジョブを作成して即返す（重い収集は待たない）。

    実体はデーモンスレッドで実行され、進捗・結果は GET /discovery/jobs/{job_id} で
    取得する。この POST 内で収集・外部 HTTP は行わない（画面を固めない）。
    同一条件（platform × query × limit）の進行中ジョブがあれば再利用する。

    実取得未対応（準備中）のプラットフォームは 400 で弾く（実行ボタンは UI 側でも無効）。
    """
    platform = payload.source_platform
    if not is_live_fetch(platform):
        raise HTTPException(
            status_code=400,
            detail=f"{platform} は実サイト取得が未接続（準備中）のため実行できません。",
        )

    # 実取得プラットフォームは取得直後に自動スコアリングして「営業すべき順」に
    # 並べられる状態にする（UI チェックボックスに関わらず既定で評価する）。
    job, is_new = discovery_job_service.create_job(
        db,
        source_platform=platform,
        query=payload.query,
        limit=payload.limit,
        auto_score=True,
    )
    return DiscoveryRunJobResponse(
        job_id=job.id, platform=job.source_platform, status=job.status,
        reused=not is_new,
    )


@router.get("/jobs/{job_id}", response_model=DiscoveryJobOut)
def get_discovery_job(
    job_id: int, db: Session = Depends(get_db)
) -> DiscoveryJobOut:
    """発掘ジョブの進捗・結果を取得する（読み取り専用。収集は起動しない）。"""
    job = discovery_job_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="発掘ジョブが見つかりません")
    return job


@router.post("/products", response_model=DiscoveredProductOut)
def create_product(
    payload: DiscoveredProductCreate, db: Session = Depends(get_db)
) -> DiscoveredProductOut:
    """商品候補を登録する（source_url 重複時は既存を再利用）。

    auto_score=true のときのみ、登録直後に自動スコアリングする。
    """
    data = payload.model_dump()
    auto_score = data.pop("auto_score", False)
    product, _created = discovery_service.create(db, data, auto_score=auto_score)
    return product


@router.get("/products", response_model=list[DiscoveredProductOut])
def list_products(
    platform: str | None = Query(None, description="発掘元プラットフォームで絞り込み"),
    status: str | None = Query(None, description="ステータスで絞り込み"),
    category: str | None = Query(None, description="カテゴリで絞り込み"),
    min_score: int | None = Query(None, description="総合発掘スコアの下限"),
    sort: str = Query("score", description="score / created"),
    db: Session = Depends(get_db),
) -> list[DiscoveredProductOut]:
    """商品候補一覧を取得する（フィルター・並び替え対応）。"""
    return discovery_service.list_products(
        db, platform=platform, status=status, category=category,
        min_score=min_score, sort=sort,
    )


@router.get("/products/{product_id}", response_model=DiscoveredProductOut)
def get_product(
    product_id: int, db: Session = Depends(get_db)
) -> DiscoveredProductOut:
    product = discovery_service.get(db, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="商品候補が見つかりません")
    return product


@router.post("/products/{product_id}/score", response_model=DiscoveredProductOut)
def score_product(
    product_id: int, db: Session = Depends(get_db)
) -> DiscoveredProductOut:
    """保存済み商品を AI Discovery Scoring で評価し、スコアを更新して返す。

    実 API キーが無くてもルールベースにフォールバックして必ずスコアを付与する。
    """
    product = discovery_service.score_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="商品候補が見つかりません")
    return product


@router.post(
    "/products/{product_id}/contact-intelligence",
    response_model=DiscoveryContactIntelligenceResult,
)
def start_contact_intelligence(
    product_id: int, db: Session = Depends(get_db)
) -> DiscoveryContactIntelligenceResult:
    """発掘商品から Contact Intelligence（連絡先探索）を開始する。

    official_website_url（無ければ source_url）を使って既存の Contact Discovery を
    起動し、作成された contact_discovery_id を商品に保存する。すでに連携済みなら
    既存 id を返す。商品が無ければ 404、URL が無ければ 400。
    """
    result = discovery_service.start_contact_intelligence_from_product(db, product_id)
    if result is None:
        raise HTTPException(status_code=404, detail="商品候補が見つかりません")
    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.patch("/products/{product_id}", response_model=DiscoveredProductOut)
def update_product(
    product_id: int,
    payload: DiscoveredProductUpdate,
    db: Session = Depends(get_db),
) -> DiscoveredProductOut:
    """商品候補を更新する（渡されたフィールドのみ更新）。"""
    provided = payload.model_dump(exclude_unset=True)
    try:
        product = discovery_service.update(db, product_id, provided)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if product is None:
        raise HTTPException(status_code=404, detail="商品候補が見つかりません")
    return product
