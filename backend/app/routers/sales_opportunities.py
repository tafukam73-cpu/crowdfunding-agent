"""営業案件管理 API（Contact Intelligence v5）。

- POST /sales-opportunities/from-contact-discovery/{contact_discovery_id}
    Contact Intelligence 結果から営業案件を作成（冪等：既存があれば再利用）
- GET  /sales-opportunities                 一覧（status/priority/min_score/sort で絞り込み）
- GET  /sales-opportunities/{id}            詳細取得
- PATCH /sales-opportunities/{id}           status/next_action/期限/メモ を更新
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.sales_opportunity import (
    SalesOpportunityOut,
    SalesOpportunityUpdate,
)
from app.services import sales_opportunity_service

router = APIRouter(prefix="/sales-opportunities", tags=["sales-opportunities"])


@router.post(
    "/from-contact-discovery/{contact_discovery_id}",
    response_model=SalesOpportunityOut,
)
def create_from_contact_discovery(
    contact_discovery_id: int, db: Session = Depends(get_db)
) -> SalesOpportunityOut:
    """Contact Intelligence 結果から営業案件を作成する（冪等）。"""
    try:
        opp, _created = sales_opportunity_service.create_from_contact_discovery(
            db, contact_discovery_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return opp


@router.get("", response_model=list[SalesOpportunityOut])
def list_opportunities(
    status: str | None = Query(None, description="ステータスで絞り込み"),
    sales_priority: str | None = Query(None, description="優先度（high/medium/low）"),
    min_score: int | None = Query(None, description="営業スコアの下限"),
    sort: str = Query("score", description="score / due_date / created"),
    db: Session = Depends(get_db),
) -> list[SalesOpportunityOut]:
    """営業案件一覧を取得する（フィルター・並び替え対応）。"""
    return sales_opportunity_service.list_opportunities(
        db, status=status, sales_priority=sales_priority,
        min_score=min_score, sort=sort,
    )


@router.get("/{opportunity_id}", response_model=SalesOpportunityOut)
def get_opportunity(
    opportunity_id: int, db: Session = Depends(get_db)
) -> SalesOpportunityOut:
    opp = sales_opportunity_service.get(db, opportunity_id)
    if opp is None:
        raise HTTPException(status_code=404, detail="営業案件が見つかりません")
    return opp


@router.patch("/{opportunity_id}", response_model=SalesOpportunityOut)
def update_opportunity(
    opportunity_id: int,
    payload: SalesOpportunityUpdate,
    db: Session = Depends(get_db),
) -> SalesOpportunityOut:
    """営業案件を更新する（渡されたフィールドのみ更新）。"""
    # 明示的に渡されたフィールドのみを service に渡す（未指定は据え置き）。
    provided = payload.model_dump(exclude_unset=True)
    try:
        opp = sales_opportunity_service.update(db, opportunity_id, **provided)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if opp is None:
        raise HTTPException(status_code=404, detail="営業案件が見つかりません")
    return opp
