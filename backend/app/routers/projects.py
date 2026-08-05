"""案件 CRUD API。

- GET    /projects            一覧（フィルタ・ソート・ページング）
- POST   /projects            新規作成
- GET    /projects/{id}       詳細
- PUT    /projects/{id}       更新（部分更新）
- PATCH  /projects/{id}/status ステータス更新
- DELETE /projects/{id}       削除
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.project import ProjectStatus, SourceSite
from app.schemas.project import (
    ProjectArchiveRequest,
    ProjectBulkArchiveRequest,
    ProjectBulkArchiveResult,
    ProjectBulkUnarchiveRequest,
    ProjectCreate,
    ProjectListOut,
    ProjectOut,
    ProjectStatusUpdate,
    ProjectUpdate,
)
from app.services import project_service

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=ProjectListOut)
def list_projects(
    db: Session = Depends(get_db),
    site: SourceSite | None = Query(None, description="収集元サイトで絞り込み"),
    status_: ProjectStatus | None = Query(None, alias="status", description="営業ステータスで絞り込み"),
    sales_status: str | None = Query(None, description="営業状況（sales_status）で絞り込み"),
    category: str | None = Query(None, description="カテゴリで絞り込み"),
    q: str | None = Query(None, description="案件名の部分一致検索"),
    min_score: int | None = Query(None, ge=0, le=100, description="AI総合スコアの下限"),
    recommendation: str | None = Query(
        None, pattern="^(high|mid|low)$", description="推奨度で絞り込み"
    ),
    qualification: str | None = Query(
        None,
        pattern="^(blocked|review|clear)$",
        description=(
            "営業対象除外判定で絞り込み（最新の pre_research 判定。"
            "送信可否 pre_outreach は対象外）"
        ),
    ),
    archived: bool = Query(
        False,
        description="true なら営業対象外（除外済み）案件のみ、false（既定）なら対象内のみ",
    ),
    sort: str = Query("created_at", description="並び替えキー"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> ProjectListOut:
    items, total = project_service.list_projects(
        db,
        site=site,
        status=status_,
        sales_status=sales_status,
        category=category,
        q=q,
        min_score=min_score,
        recommendation=recommendation,
        qualification=qualification,
        archived=archived,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    return ProjectListOut(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectOut:
    return project_service.create_project(db, payload)


@router.post("/archive", response_model=ProjectBulkArchiveResult)
def bulk_archive(
    payload: ProjectBulkArchiveRequest, db: Session = Depends(get_db)
) -> ProjectBulkArchiveResult:
    """複数案件を一括で営業対象外にする（更新件数を返す）。"""
    updated = project_service.archive_projects(db, payload.ids, payload.reason)
    return ProjectBulkArchiveResult(updated=updated)


@router.post("/unarchive", response_model=ProjectBulkArchiveResult)
def bulk_unarchive(
    payload: ProjectBulkUnarchiveRequest, db: Session = Depends(get_db)
) -> ProjectBulkArchiveResult:
    """複数案件を一括で復元する（更新件数を返す）。"""
    updated = project_service.unarchive_projects(db, payload.ids)
    return ProjectBulkArchiveResult(updated=updated)


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)) -> ProjectOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return project


@router.put("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int, payload: ProjectUpdate, db: Session = Depends(get_db)
) -> ProjectOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return project_service.update_project(db, project, payload)


@router.patch("/{project_id}/status", response_model=ProjectOut)
def update_status(
    project_id: int, payload: ProjectStatusUpdate, db: Session = Depends(get_db)
) -> ProjectOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return project_service.update_status(db, project, payload.status)


@router.patch("/{project_id}/archive", response_model=ProjectOut)
def archive_project(
    project_id: int,
    payload: ProjectArchiveRequest,
    db: Session = Depends(get_db),
) -> ProjectOut:
    """案件を営業対象外にする（ソフトデリート。理由は任意で保存）。"""
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return project_service.archive_project(db, project, payload.reason)


@router.patch("/{project_id}/unarchive", response_model=ProjectOut)
def unarchive_project(project_id: int, db: Session = Depends(get_db)) -> ProjectOut:
    """営業対象外を解除して通常一覧へ戻す（復元）。"""
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return project_service.unarchive_project(db, project)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_project(project_id: int, db: Session = Depends(get_db)) -> Response:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    project_service.delete_project(db, project)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
