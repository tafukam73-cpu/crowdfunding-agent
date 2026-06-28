"""営業ワークフロー / 今日営業する案件 / 営業ダッシュボード API。

- GET   /projects/{id}/workflow        営業ワークフロー（ステップ・チャネル・優先順位）
- PATCH /projects/{id}/sales-status     営業状況を更新（CRM へ営業履歴を自動記録）
- GET   /sales/today                    今日営業すべき案件（準備完了・未営業）
- GET   /sales/dashboard                営業ダッシュボードの集計
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.project import ProjectOut
from app.schemas.sales import (
    SalesDashboardOut,
    SalesStatusUpdate,
    TodayListOut,
    WorkflowOut,
)
from app.services import project_service, workflow_service

router = APIRouter(tags=["sales"])


@router.get("/projects/{project_id}/workflow", response_model=WorkflowOut)
def get_workflow(project_id: int, db: Session = Depends(get_db)) -> WorkflowOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return workflow_service.compute_workflow(db, project)


@router.patch("/projects/{project_id}/sales-status", response_model=ProjectOut)
def update_sales_status(
    project_id: int, payload: SalesStatusUpdate, db: Session = Depends(get_db)
) -> ProjectOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return project_service.update_sales_status(db, project, payload.sales_status)


@router.get("/sales/today", response_model=TodayListOut)
def sales_today(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> TodayListOut:
    return TodayListOut(items=workflow_service.today_projects(db, limit=limit))


@router.get("/sales/dashboard", response_model=SalesDashboardOut)
def sales_dashboard(db: Session = Depends(get_db)) -> SalesDashboardOut:
    return SalesDashboardOut(**workflow_service.dashboard_summary(db))
