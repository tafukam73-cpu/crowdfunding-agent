"""日本販売状況チェック API。

- POST /projects/{id}/japan-sales-check   チェックを実行（同期）して保存
- GET  /projects/{id}/japan-sales-check    最新のチェック結果を取得

失敗してもアプリは落とさない。Claude の JSON パース失敗等は status=failed として
保存され、200 で返る（画面側で失敗表示する）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.japan_sales_check import JapanSalesCheckOut
from app.services import japan_sales_service, project_service

logger = logging.getLogger("router.japan_sales")

router = APIRouter(tags=["japan-sales"])


@router.post(
    "/projects/{project_id}/japan-sales-check", response_model=JapanSalesCheckOut
)
def run_japan_sales_check(
    project_id: int, db: Session = Depends(get_db)
) -> JapanSalesCheckOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return japan_sales_service.run_check(db, project)


@router.get(
    "/projects/{project_id}/japan-sales-check", response_model=JapanSalesCheckOut
)
def get_japan_sales_check(project_id: int, db: Session = Depends(get_db)):
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    row = japan_sales_service.get_latest(db, project_id)
    if row is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return row
