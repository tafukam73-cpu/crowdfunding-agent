"""CRM（営業管理）の業務ロジック。"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from app.models.crm import Contact, CrmStatus, Maker, SalesActivity
from app.models.project import Project
from app.schemas.crm import (
    ActivityCreate,
    ContactCreate,
    ContactUpdate,
    MakerCreate,
    MakerUpdate,
)

SORTABLE = {
    "created_at": Maker.created_at,
    "updated_at": Maker.updated_at,
    "name": Maker.name,
    "next_action_date": Maker.next_action_date,
    "status": Maker.status,
}


# --- メーカー ---
def list_makers(
    db: Session,
    *,
    status: CrmStatus | None = None,
    q: str | None = None,
    sort: str = "updated_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Maker], int]:
    conditions = []
    if status is not None:
        conditions.append(Maker.status == status.value)
    if q:
        conditions.append(Maker.name.ilike(f"%{q}%"))

    base = select(Maker)
    count_stmt = select(func.count()).select_from(Maker)
    for c in conditions:
        base = base.where(c)
        count_stmt = count_stmt.where(c)

    total = db.scalar(count_stmt) or 0
    sort_col = SORTABLE.get(sort, Maker.updated_at)
    direction = asc if order == "asc" else desc
    base = base.order_by(direction(sort_col).nullslast())
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    base = base.offset((page - 1) * page_size).limit(page_size)
    return list(db.scalars(base)), total


def get_maker(db: Session, maker_id: int) -> Maker | None:
    return db.get(Maker, maker_id)


def create_maker(db: Session, data: MakerCreate) -> Maker:
    payload = data.model_dump()
    payload["status"] = data.status.value
    maker = Maker(**payload)
    db.add(maker)
    db.commit()
    db.refresh(maker)
    return maker


def update_maker(db: Session, maker: Maker, data: MakerUpdate) -> Maker:
    payload = data.model_dump(exclude_unset=True)
    for key, value in payload.items():
        if key == "status" and value is not None:
            value = value.value
        setattr(maker, key, value)
    db.commit()
    db.refresh(maker)
    return maker


def delete_maker(db: Session, maker: Maker) -> None:
    # 子（担当者・営業履歴）を明示的に削除（SQLite の FK カスケード非依存）
    db.query(SalesActivity).filter(SalesActivity.maker_id == maker.id).delete()
    db.query(Contact).filter(Contact.maker_id == maker.id).delete()
    # 紐づく案件のリンクを解除
    db.query(Project).filter(Project.maker_id == maker.id).update(
        {Project.maker_id: None}
    )
    db.delete(maker)
    db.commit()


def get_project_ids(db: Session, maker_id: int) -> list[int]:
    return list(
        db.scalars(select(Project.id).where(Project.maker_id == maker_id))
    )


def create_from_project(db: Session, project: Project) -> Maker:
    """案件のメーカー情報からメーカーを作成し、案件をリンクする。

    既に同案件がリンク済みならそのメーカーを返す（冪等）。
    """
    if project.maker_id:
        existing = db.get(Maker, project.maker_id)
        if existing is not None:
            return existing

    maker = Maker(
        name=project.maker_name or project.title[:255],
        website_url=project.maker_url,
        status=CrmStatus.lead.value,
        notes=project.contact_info,
    )
    db.add(maker)
    db.commit()
    db.refresh(maker)

    project.maker_id = maker.id
    db.commit()
    return maker


# --- 担当者 ---
def get_contact(db: Session, contact_id: int) -> Contact | None:
    return db.get(Contact, contact_id)


def list_contacts(db: Session, maker_id: int) -> list[Contact]:
    return list(
        db.scalars(
            select(Contact).where(Contact.maker_id == maker_id).order_by(Contact.id)
        )
    )


def add_contact(db: Session, maker_id: int, data: ContactCreate) -> Contact:
    contact = Contact(maker_id=maker_id, **data.model_dump())
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def update_contact(db: Session, contact: Contact, data: ContactUpdate) -> Contact:
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(contact, key, value)
    db.commit()
    db.refresh(contact)
    return contact


def delete_contact(db: Session, contact: Contact) -> None:
    db.delete(contact)
    db.commit()


# --- 営業履歴 ---
def list_activities(db: Session, maker_id: int) -> list[SalesActivity]:
    return list(
        db.scalars(
            select(SalesActivity)
            .where(SalesActivity.maker_id == maker_id)
            .order_by(desc(SalesActivity.occurred_at), desc(SalesActivity.id))
        )
    )


def add_activity(db: Session, maker_id: int, data: ActivityCreate) -> SalesActivity:
    payload = data.model_dump()
    payload["kind"] = data.kind.value
    if payload.get("occurred_at") is None:
        payload["occurred_at"] = datetime.now(timezone.utc)
    activity = SalesActivity(maker_id=maker_id, **payload)
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def get_activity(db: Session, activity_id: int) -> SalesActivity | None:
    return db.get(SalesActivity, activity_id)


def delete_activity(db: Session, activity: SalesActivity) -> None:
    db.delete(activity)
    db.commit()


# --- リマインダー ---
def reminders(db: Session, within_days: int | None = None) -> list[dict]:
    """次回アクション日が設定されたメーカーを返す（期限切れ→近い順）。

    within_days を指定すると「今日 + within_days」までのものに絞る。
    """
    stmt = select(Maker).where(Maker.next_action_date.is_not(None))
    if within_days is not None:
        limit_date = date.today().toordinal() + within_days
        stmt = stmt.where(Maker.next_action_date <= date.fromordinal(limit_date))
    stmt = stmt.order_by(asc(Maker.next_action_date))

    today = date.today()
    out: list[dict] = []
    for m in db.scalars(stmt):
        out.append(
            {
                "maker_id": m.id,
                "maker_name": m.name,
                "status": m.status,
                "next_action": m.next_action,
                "next_action_date": m.next_action_date,
                "overdue": m.next_action_date < today,
            }
        )
    return out
