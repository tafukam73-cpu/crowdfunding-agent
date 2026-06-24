"""使用量・コスト集計 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.usage import UsageSummaryOut
from app.services import usage_service

router = APIRouter(tags=["usage"])


@router.get("/usage/summary", response_model=UsageSummaryOut)
def usage_summary(db: Session = Depends(get_db)) -> UsageSummaryOut:
    """本日 / 今月 / 累計 のコストとトークンを返す。"""
    return usage_service.summary(db)
