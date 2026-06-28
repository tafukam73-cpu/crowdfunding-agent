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
    category: str | None = Query(None, description="カテゴリで絞り込み"),
    q: str | None = Query(None, description="案件名の部分一致検索"),
    min_score: int | None = Query(None, ge=0, le=100, description="AI総合スコアの下限"),
    recommendation: str | None = Query(
        None, pattern="^(high|mid|low)$", description="推奨度で絞り込み"
    ),
    candidates_only: bool = Query(
        False, description="営業対象候補のみ（営業対象外っぽい Ulule 案件を除外）"
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
        category=category,
        q=q,
        min_score=min_score,
        recommendation=recommendation,
        candidates_only=candidates_only,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )
    return ProjectListOut(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectOut:
    return project_service.create_project(db, payload)


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
