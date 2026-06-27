"""案件の業務ロジック（CRUD・検索）。"""
from __future__ import annotations

from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from app.models.project import (
    JAPANESE_SUCCESS_SITES,
    SALES_TARGET_SITES,
    Project,
    ProjectStatus,
    SourceSite,
)
from app.schemas.project import ProjectCreate, ProjectUpdate

# 営業対象サイトの値（一覧クエリ用）。Makuake / GreenFunding は除外する。
_SALES_TARGET_VALUES = [s.value for s in SALES_TARGET_SITES]
_JAPANESE_SUCCESS_VALUES = {s.value for s in JAPANESE_SUCCESS_SITES}

# 並び替えに使えるカラム
SORTABLE = {
    "created_at": Project.created_at,
    "updated_at": Project.updated_at,
    "raised_amount": Project.raised_amount,
    "backers_count": Project.backers_count,
    "end_date": Project.end_date,
    "title": Project.title,
    "latest_score": Project.latest_score,
}


def get_project(db: Session, project_id: int) -> Project | None:
    return db.get(Project, project_id)


def list_projects(
    db: Session,
    *,
    site: SourceSite | None = None,
    status: ProjectStatus | None = None,
    category: str | None = None,
    q: str | None = None,
    min_score: int | None = None,
    recommendation: str | None = None,
    sort: str = "created_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Project], int]:
    """フィルタ・ソート・ページング付きで案件を取得する。

    Returns: (items, total)
    """
    # 営業対象（Kickstarter / Indiegogo / Wadiz）のみ。日本の成功事例
    # （Makuake / GreenFunding）が混入していても一覧には出さない。
    conditions = [Project.source_site.in_(_SALES_TARGET_VALUES)]
    if site is not None:
        conditions.append(Project.source_site == site.value)
    if status is not None:
        conditions.append(Project.status == status.value)
    if category:
        conditions.append(Project.category == category)
    if q:
        like = f"%{q}%"
        conditions.append(Project.title.ilike(like))
    if min_score is not None:
        conditions.append(Project.latest_score >= min_score)
    if recommendation:
        conditions.append(Project.latest_recommendation == recommendation)

    base = select(Project)
    count_stmt = select(func.count()).select_from(Project)
    for cond in conditions:
        base = base.where(cond)
        count_stmt = count_stmt.where(cond)

    total = db.scalar(count_stmt) or 0

    sort_col = SORTABLE.get(sort, Project.created_at)
    direction = asc if order == "asc" else desc
    # スコア等で NULL（未評価/欠損）は常に最後へ
    base = base.order_by(direction(sort_col).nullslast())

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    base = base.offset((page - 1) * page_size).limit(page_size)

    items = list(db.scalars(base))
    return items, total


def create_project(db: Session, data: ProjectCreate) -> Project:
    project = Project(**data.model_dump())
    # Enum -> 値（文字列）へ
    project.source_site = data.source_site.value
    project.status = data.status.value
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def update_project(db: Session, project: Project, data: ProjectUpdate) -> Project:
    payload = data.model_dump(exclude_unset=True)
    for key, value in payload.items():
        if key in {"source_site", "status"} and value is not None:
            value = value.value  # Enum -> 文字列
        setattr(project, key, value)
    db.commit()
    db.refresh(project)
    return project


def update_status(db: Session, project: Project, status: ProjectStatus) -> Project:
    project.status = status.value
    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, project: Project) -> None:
    db.delete(project)
    db.commit()


# スクレイピング取り込み時に更新しないフィールド（ユーザー管理 / メタ）
_UPSERT_SKIP = {"status"}


def upsert_by_source_url(db: Session, data: ProjectCreate) -> tuple[Project, bool]:
    """source_url をキーに upsert する。

    既存があれば収集項目を更新（営業ステータスは保持）、なければ新規作成。
    Returns: (project, created)  created=True なら新規。

    注意：コミットは行わない。呼び出し側（collector）でまとめてコミットする。
    """
    # 日本の成功事例（Makuake / GreenFunding）は projects には保存しない。
    # これらは japanese_success_service が japanese_success_projects に収集する。
    if data.source_site.value in _JAPANESE_SUCCESS_VALUES:
        raise ValueError(
            f"{data.source_site.value} は営業対象外のため projects に保存できません"
            "（japanese_success_projects に保存してください）"
        )

    existing: Project | None = None
    if data.source_url:
        existing = db.scalar(
            select(Project).where(Project.source_url == data.source_url)
        )

    payload = data.model_dump()
    payload["source_site"] = data.source_site.value
    payload["status"] = data.status.value

    if existing is None:
        project = Project(**payload)
        db.add(project)
        return project, True

    for key, value in payload.items():
        if key in _UPSERT_SKIP:
            continue
        setattr(existing, key, value)
    return existing, False
