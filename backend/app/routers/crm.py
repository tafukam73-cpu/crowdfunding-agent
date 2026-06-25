"""CRM（営業管理）API。

- メーカー（企業）CRUD、案件からのメーカー作成
- 担当者 CRUD
- 営業履歴の記録・参照
- リマインダー（次回アクション日）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.crm import CrmStatus
from app.schemas.crm import (
    ActivityCreate,
    ActivityOut,
    ContactCreate,
    ContactOut,
    ContactUpdate,
    MakerCreate,
    MakerDetailOut,
    MakerListOut,
    MakerOut,
    MakerUpdate,
    ReminderOut,
)
from app.services import crm_service, project_service

router = APIRouter(prefix="/crm", tags=["crm"])


# --- メーカー ---
@router.get("/makers", response_model=MakerListOut)
def list_makers(
    db: Session = Depends(get_db),
    status: CrmStatus | None = None,
    q: str | None = None,
    sort: str = "updated_at",
    order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> MakerListOut:
    items, total = crm_service.list_makers(
        db, status=status, q=q, sort=sort, order=order, page=page, page_size=page_size
    )
    return MakerListOut(
        items=[MakerOut.model_validate(m) for m in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/makers", response_model=MakerOut, status_code=201)
def create_maker(data: MakerCreate, db: Session = Depends(get_db)) -> MakerOut:
    return crm_service.create_maker(db, data)


@router.post("/makers/from-project/{project_id}", response_model=MakerOut, status_code=201)
def create_maker_from_project(
    project_id: int, db: Session = Depends(get_db)
) -> MakerOut:
    """海外案件のメーカー情報からメーカーを作成し、案件をリンクする。"""
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return crm_service.create_from_project(db, project)


@router.get("/reminders", response_model=list[ReminderOut])
def reminders(
    db: Session = Depends(get_db),
    within_days: int | None = Query(None, ge=0, le=365),
) -> list[ReminderOut]:
    return [ReminderOut(**r) for r in crm_service.reminders(db, within_days=within_days)]


@router.get("/makers/{maker_id}", response_model=MakerDetailOut)
def get_maker(maker_id: int, db: Session = Depends(get_db)) -> MakerDetailOut:
    maker = crm_service.get_maker(db, maker_id)
    if maker is None:
        raise HTTPException(status_code=404, detail="メーカーが見つかりません")
    base = MakerOut.model_validate(maker)
    return MakerDetailOut(
        **base.model_dump(),
        contacts=[ContactOut.model_validate(c) for c in crm_service.list_contacts(db, maker_id)],
        activities=[ActivityOut.model_validate(a) for a in crm_service.list_activities(db, maker_id)],
        project_ids=crm_service.get_project_ids(db, maker_id),
    )


@router.patch("/makers/{maker_id}", response_model=MakerOut)
def update_maker(
    maker_id: int, data: MakerUpdate, db: Session = Depends(get_db)
) -> MakerOut:
    maker = crm_service.get_maker(db, maker_id)
    if maker is None:
        raise HTTPException(status_code=404, detail="メーカーが見つかりません")
    return crm_service.update_maker(db, maker, data)


@router.delete("/makers/{maker_id}")
def delete_maker(maker_id: int, db: Session = Depends(get_db)) -> dict:
    maker = crm_service.get_maker(db, maker_id)
    if maker is None:
        raise HTTPException(status_code=404, detail="メーカーが見つかりません")
    crm_service.delete_maker(db, maker)
    return {"deleted": True}


# --- 担当者 ---
@router.post("/makers/{maker_id}/contacts", response_model=ContactOut, status_code=201)
def add_contact(
    maker_id: int, data: ContactCreate, db: Session = Depends(get_db)
) -> ContactOut:
    if crm_service.get_maker(db, maker_id) is None:
        raise HTTPException(status_code=404, detail="メーカーが見つかりません")
    return crm_service.add_contact(db, maker_id, data)


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
def update_contact(
    contact_id: int, data: ContactUpdate, db: Session = Depends(get_db)
) -> ContactOut:
    contact = crm_service.get_contact(db, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="担当者が見つかりません")
    return crm_service.update_contact(db, contact, data)


@router.delete("/contacts/{contact_id}")
def delete_contact(contact_id: int, db: Session = Depends(get_db)) -> dict:
    contact = crm_service.get_contact(db, contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="担当者が見つかりません")
    crm_service.delete_contact(db, contact)
    return {"deleted": True}


# --- 営業履歴 ---
@router.get("/makers/{maker_id}/activities", response_model=list[ActivityOut])
def list_activities(maker_id: int, db: Session = Depends(get_db)) -> list[ActivityOut]:
    if crm_service.get_maker(db, maker_id) is None:
        raise HTTPException(status_code=404, detail="メーカーが見つかりません")
    return crm_service.list_activities(db, maker_id)


@router.post("/makers/{maker_id}/activities", response_model=ActivityOut, status_code=201)
def add_activity(
    maker_id: int, data: ActivityCreate, db: Session = Depends(get_db)
) -> ActivityOut:
    if crm_service.get_maker(db, maker_id) is None:
        raise HTTPException(status_code=404, detail="メーカーが見つかりません")
    return crm_service.add_activity(db, maker_id, data)


@router.delete("/activities/{activity_id}")
def delete_activity(activity_id: int, db: Session = Depends(get_db)) -> dict:
    activity = crm_service.get_activity(db, activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="営業履歴が見つかりません")
    crm_service.delete_activity(db, activity)
    return {"deleted": True}
