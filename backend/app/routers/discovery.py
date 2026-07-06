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
    DiscoveryRunRequest,
    DiscoveryRunResult,
)
from app.services import (
    discovery_crawler_service,
    discovery_fetch,
    discovery_service,
)

router = APIRouter(prefix="/discovery", tags=["discovery"])

# 実サイト取得を有効化するプラットフォーム（v1-6 は Kickstarter のみ）。
# ここに無いプラットフォームは fetch 未接続のまま（従来どおり 0 件・外部送信なし）。
_LIVE_FETCH_PLATFORMS = {"kickstarter"}


@router.post("/run", response_model=DiscoveryRunResult)
def run_discovery(
    payload: DiscoveryRunRequest, db: Session = Depends(get_db)
) -> DiscoveryRunResult:
    """Discovery Crawler Framework を 1 回実行し、結果サマリを返す。

    source_platform に対応する adapter で候補を収集し、URL 正規化・重複排除の上で
    discovered_products に保存する。auto_score=True なら保存時に自動スコアリングする。

    v1-6：Kickstarter は実サイト（discover/advanced JSON）から取得する。取得は
    robots 尊重・レート制限・タイムアウト・専用 User-Agent 付き（``discovery_fetch``）。
    取得失敗は例外で落とさず discovery_runs.error_message に記録する。
    その他プラットフォームは fetch 未接続のまま（候補 0 件・外部送信なし）。
    """
    # Kickstarter のみ実取得の fetch_fn を注入。他は None（未接続）のまま。
    fetch_fn = None
    if payload.source_platform in _LIVE_FETCH_PLATFORMS:
        fetch_fn = discovery_fetch.build_http_fetcher()

    # 実取得プラットフォームは取得直後に自動スコアリングして「営業すべき順」に
    # 並べられる状態にする（UI チェックボックスに関わらず既定で評価する）。
    auto_score = payload.auto_score or payload.source_platform in _LIVE_FETCH_PLATFORMS

    try:
        result = discovery_crawler_service.run(
            db,
            source_platform=payload.source_platform,
            query=payload.query,
            limit=payload.limit,
            auto_score=auto_score,
            fetch_fn=fetch_fn,
        )
    finally:
        # 実取得 fetcher（Playwright ブラウザ等）を必ず解放する。
        if fetch_fn is not None:
            fetch_fn.close()
    return result


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
