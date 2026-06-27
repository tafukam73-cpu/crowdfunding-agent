"""AI 企業リサーチ API。

- POST /projects/{id}/company-research   リサーチを実行（同期）して保存
- GET  /projects/{id}/company-research    最新のリサーチ結果を取得

失敗してもアプリは落とさない。Claude の JSON パース失敗等は research_status=failed
として保存され、200 で返る（画面側で失敗表示する）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.company_research import CompanyResearchOut
from app.services import company_research_service, project_service

logger = logging.getLogger("router.company_research")

router = APIRouter(tags=["company-research"])


@router.post(
    "/projects/{project_id}/company-research", response_model=CompanyResearchOut
)
def run_company_research(
    project_id: int, db: Session = Depends(get_db)
) -> CompanyResearchOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    # 失敗時も failed として保存され row が返る（例外送出しない）
    row = company_research_service.run_research(db, project)
    return row


@router.get(
    "/projects/{project_id}/company-research", response_model=CompanyResearchOut
)
def get_company_research(project_id: int, db: Session = Depends(get_db)):
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    row = company_research_service.get_latest(db, project_id)
    if row is None:
        # 未実行は 204 No Content（フロントは「未実行」と表示）。
        # Response を直接返して response_model 検証を回避する。
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return row
