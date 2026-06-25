"""日本クラファン成功案件 API。

- GET  /japanese-success                  成功案件一覧（フィルタ・ソート・ページング）
- POST /japanese-success/collect          Makuake から成功案件を収集（同期・モック）
- GET  /projects/{id}/similar-japanese    海外案件に類似する日本の成功事例

海外案件（projects）とは別管理の「比較用データ」。営業判断の根拠に使う。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.japanese_success import (
    CollectResult,
    JapaneseSuccessListOut,
    JapaneseSuccessOut,
    SimilarSuccessOut,
)
from app.services import japanese_success_service, project_service

router = APIRouter(tags=["japanese-success"])


@router.get("/japanese-success", response_model=JapaneseSuccessListOut)
def list_japanese_success(
    db: Session = Depends(get_db),
    platform: str | None = None,
    category: str | None = None,
    q: str | None = None,
    sort: str = "raised_amount",
    order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> JapaneseSuccessListOut:
    items, total = japanese_success_service.list_items(
        db,
        platform=platform,
        category=category,
        q=q,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    return JapaneseSuccessListOut(
        items=[JapaneseSuccessOut.model_validate(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/japanese-success/collect", response_model=CollectResult)
def collect_japanese_success(
    db: Session = Depends(get_db),
    platform: str | None = Query(
        None, description="収集対象。未指定で Makuake + GreenFunding を一括収集"
    ),
) -> CollectResult:
    """日本クラファンの成功案件を収集して保存する（同期・現状モック）。

    - platform 指定あり：指定プラットフォームのみ収集
    - platform 指定なし：Makuake + GreenFunding を一括収集
    """
    if platform is not None and platform not in japanese_success_service.JAPANESE_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=(
                "未対応のプラットフォームです。"
                f"指定可能: {', '.join(japanese_success_service.JAPANESE_PLATFORMS)}"
            ),
        )
    return japanese_success_service.collect(db, platform=platform)


@router.get(
    "/projects/{project_id}/similar-japanese",
    response_model=list[SimilarSuccessOut],
)
def similar_japanese(
    project_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(3, ge=1, le=10),
) -> list[SimilarSuccessOut]:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")

    results = japanese_success_service.find_similar(db, project, limit=limit)
    out: list[SimilarSuccessOut] = []
    for obj, score, reasons in results:
        base = JapaneseSuccessOut.model_validate(obj)
        out.append(
            SimilarSuccessOut(
                **base.model_dump(),
                match_score=score,
                match_reasons=reasons,
            )
        )
    return out
