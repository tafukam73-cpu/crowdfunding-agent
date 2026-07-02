"""発掘商品候補 API（Discovery Engine v1-1）。

- POST  /discovery/products         商品候補を登録（source_url 重複時は既存を再利用）
- GET   /discovery/products         一覧（platform/status/category/min_score/sort で絞り込み）
- GET   /discovery/products/{id}    詳細取得
- PATCH /discovery/products/{id}    更新（渡されたフィールドのみ）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.discovery import (
    DiscoveredProductCreate,
    DiscoveredProductOut,
    DiscoveredProductUpdate,
)
from app.services import discovery_service

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.post("/products", response_model=DiscoveredProductOut)
def create_product(
    payload: DiscoveredProductCreate, db: Session = Depends(get_db)
) -> DiscoveredProductOut:
    """商品候補を登録する（source_url 重複時は既存を再利用）。"""
    product, _created = discovery_service.create(db, payload.model_dump())
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
