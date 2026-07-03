"""日本市場機会 分析 API（Japan Opportunity Engine v1-2）。

- POST  /japan-opportunity/analyses                          分析を作成
- GET   /japan-opportunity/analyses                          一覧（product/min_score/sort）
- GET   /japan-opportunity/analyses/{id}                     詳細取得
- PATCH /japan-opportunity/analyses/{id}                     更新（渡された項目のみ）
- GET   /japan-opportunity/products/{product_id}/latest      商品の最新分析

v1-2 は保存・取得の土台のみ（評価ロジックは後続）。既存 API は変更しない。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.japan_opportunity import (
    JapanOpportunityAnalysisCreate,
    JapanOpportunityAnalysisOut,
    JapanOpportunityAnalysisUpdate,
)
from app.services import japan_opportunity_service

router = APIRouter(prefix="/japan-opportunity", tags=["japan-opportunity"])


@router.post("/analyses", response_model=JapanOpportunityAnalysisOut)
def create_analysis(
    payload: JapanOpportunityAnalysisCreate, db: Session = Depends(get_db)
) -> JapanOpportunityAnalysisOut:
    """分析を作成する。発掘商品が存在しなければ 400。"""
    data = payload.model_dump()
    product_id = data.pop("discovered_product_id")
    try:
        return japan_opportunity_service.create_analysis(db, product_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/analyze/{discovered_product_id}",
    response_model=JapanOpportunityAnalysisOut,
)
def analyze_product(
    discovered_product_id: int, db: Session = Depends(get_db)
) -> JapanOpportunityAnalysisOut:
    """発掘商品をルールベースで評価し、分析を作成して返す（v1-3）。

    発掘商品が存在しなければ 404。AI・実検索は使わない（実ネットワークなし）。
    """
    try:
        return japan_opportunity_service.analyze_product_rules(
            db, discovered_product_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/analyses", response_model=list[JapanOpportunityAnalysisOut])
def list_analyses(
    discovered_product_id: int | None = Query(
        None, description="発掘商品IDで絞り込み"
    ),
    min_score: int | None = Query(None, description="総合機会スコアの下限"),
    sort: str = Query("score_desc", description="score_desc / created_desc"),
    db: Session = Depends(get_db),
) -> list[JapanOpportunityAnalysisOut]:
    """分析一覧を取得する（フィルター・並び替え対応）。"""
    return japan_opportunity_service.list_analyses(
        db,
        discovered_product_id=discovered_product_id,
        min_score=min_score,
        sort=sort,
    )


@router.get("/analyses/{analysis_id}", response_model=JapanOpportunityAnalysisOut)
def get_analysis(
    analysis_id: int, db: Session = Depends(get_db)
) -> JapanOpportunityAnalysisOut:
    analysis = japan_opportunity_service.get_analysis(db, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="分析が見つかりません")
    return analysis


@router.patch("/analyses/{analysis_id}", response_model=JapanOpportunityAnalysisOut)
def update_analysis(
    analysis_id: int,
    payload: JapanOpportunityAnalysisUpdate,
    db: Session = Depends(get_db),
) -> JapanOpportunityAnalysisOut:
    """分析を更新する（渡されたフィールドのみ更新）。"""
    provided = payload.model_dump(exclude_unset=True)
    analysis = japan_opportunity_service.update_analysis(db, analysis_id, provided)
    if analysis is None:
        raise HTTPException(status_code=404, detail="分析が見つかりません")
    return analysis


@router.get(
    "/products/{product_id}/latest",
    response_model=JapanOpportunityAnalysisOut,
)
def get_latest_for_product(
    product_id: int, db: Session = Depends(get_db)
):
    """発掘商品の最新分析を返す。未分析なら 204。"""
    analysis = japan_opportunity_service.get_latest_for_product(db, product_id)
    if analysis is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return analysis
