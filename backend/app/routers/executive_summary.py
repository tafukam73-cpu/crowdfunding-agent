"""AI Executive Summary API。

- GET /projects/{id}/executive-summary  既存の AI 出力を統合した営業価値の要約を返す

DB 保存はせず都度算出する（MVP）。案件が無ければ 404。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.executive_summary import ExecutiveSummaryOut
from app.services import executive_summary_service, project_service

logger = logging.getLogger("router.executive_summary")

router = APIRouter(tags=["executive-summary"])


@router.get(
    "/projects/{project_id}/executive-summary", response_model=ExecutiveSummaryOut
)
def get_executive_summary(
    project_id: int, db: Session = Depends(get_db)
) -> ExecutiveSummaryOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return executive_summary_service.build_summary(db, project)
