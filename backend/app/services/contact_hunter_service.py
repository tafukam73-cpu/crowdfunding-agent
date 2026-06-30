"""Contact Hunter AI の業務ロジック。

担当者ハンター（モック/Claude）で営業担当者候補を抽出し、contact_people に保存する。
人名の捏造を防ぐため「出典 URL を持つ人物」だけを保存し、メールは既存の除外フィルタ
（contact_discovery_service）を必ず通す。実行のたびに案件の既存行を置き換える。

CRM への反映では、担当者（Contact）に氏名・役職・部署・LinkedIn・メールを保存する。
"""
from __future__ import annotations

import logging

from sqlalchemy import asc, delete, desc, select
from sqlalchemy.orm import Session

from app.ai.contact_hunter import ContactHunter, get_contact_hunter
from app.models.contact_person import ContactPerson
from app.models.crm import ActivityKind, Contact, SalesActivity
from app.models.project import Project
from app.services import (
    company_research_service,
    contact_discovery_service as cds,
    crm_service,
    usage_service,
)

logger = logging.getLogger("contact_hunter")


def _validate_email(email: str | None, *, source_site: str | None) -> str | None:
    """担当者メールを既存除外フィルタに通す（platform/sentry/no-reply 等を除外）。"""
    if not email or "@" not in email:
        return None
    site_domain = cds.source_site_email_domain(source_site)
    if cds.email_exclusion_reason(email, site_domain):
        return None
    return email


def run_hunt(
    db: Session, project: Project, hunter: ContactHunter | None = None
) -> list[ContactPerson]:
    """担当者ハントを実行し、contact_people を最新結果で置き換えて返す。

    失敗してもアプリは落とさない（空リストを返す）。出典 URL を持つ人物のみ保存し、
    メールは既存フィルタを通過したものだけ保存する。
    """
    hunter = hunter or get_contact_hunter()
    research = company_research_service.to_context(
        company_research_service.get_latest_completed(db, project.id)
    )
    try:
        result = hunter.hunt(project, research=research)
    except Exception as exc:  # noqa: BLE001  失敗は空で扱う
        logger.warning("contact hunt failed (project=%s): %s", project.id, exc)
        return get_people(db, project.id)

    # 既存行を置き換える（最新の発見結果を保持）
    db.execute(delete(ContactPerson).where(ContactPerson.project_id == project.id))

    rows: list[ContactPerson] = []
    seen: set[str] = set()
    for p in result.people:
        # 出典 URL が無い人物は採用しない（捏造防止）
        if not p.source_url:
            continue
        name = (p.name or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        email = _validate_email(p.email, source_site=project.source_site)
        row = ContactPerson(
            project_id=project.id,
            name=name,
            title=p.title,
            department=p.department,
            linkedin_url=p.linkedin_url,
            email=email,
            email_source=p.email_source if email else None,
            source_url=p.source_url,
            confidence=p.confidence,
            priority=p.priority,
            notes=p.notes or None,
        )
        db.add(row)
        rows.append(row)

    usage_service.record_usage(
        db,
        kind="contact_hunter",
        model=result.model or hunter.name,
        usage=getattr(hunter, "last_usage", None),
        project_id=project.id,
    )

    db.commit()
    for r in rows:
        db.refresh(r)
    return _sorted(rows)


def _sorted(rows: list[ContactPerson]) -> list[ContactPerson]:
    return sorted(
        rows,
        key=lambda r: ((r.priority or 0), (r.confidence or 0)),
        reverse=True,
    )


def get_people(db: Session, project_id: int) -> list[ContactPerson]:
    """案件の担当者候補を営業優先度→信頼度の降順で返す。"""
    stmt = (
        select(ContactPerson)
        .where(ContactPerson.project_id == project_id)
        .order_by(
            desc(ContactPerson.priority),
            desc(ContactPerson.confidence),
            asc(ContactPerson.id),
        )
    )
    return list(db.scalars(stmt))


def get_top_person(db: Session, project_id: int) -> ContactPerson | None:
    """営業優先度が最も高い担当者を返す（メール挨拶・Executive Summary 用）。"""
    people = get_people(db, project_id)
    return people[0] if people else None


def to_email_contact(person: ContactPerson | None) -> dict | None:
    """担当者をメール生成へ渡す dict に変換する（挨拶の Dear 用）。"""
    if person is None:
        return None
    return {
        "name": person.name,
        "title": person.title,
        "department": person.department,
        "email": person.email,
        "priority": person.priority,
    }


def apply_to_crm(
    db: Session, project: Project, person: ContactPerson
) -> tuple[int, int]:
    """担当者を CRM の Contact として追加する（氏名・役職・部署・LinkedIn・メール）。

    メーカー未登録なら案件から作成。同名（または同メール）の担当者があれば更新で
    情報を補完する。Returns: (maker_id, contact_id)
    """
    maker = crm_service.create_from_project(db, project)

    existing = None
    if person.email:
        existing = db.scalar(
            select(Contact).where(
                Contact.maker_id == maker.id, Contact.email == person.email
            )
        )
    if existing is None and person.name:
        existing = db.scalar(
            select(Contact).where(
                Contact.maker_id == maker.id, Contact.name == person.name
            )
        )

    if existing is not None:
        existing.role = existing.role or person.title
        existing.department = existing.department or person.department
        existing.linkedin_url = existing.linkedin_url or person.linkedin_url
        existing.email = existing.email or person.email
        contact_id = existing.id
    else:
        contact = Contact(
            maker_id=maker.id,
            name=person.name or f"{maker.name}（担当者）",
            role=person.title,
            department=person.department,
            linkedin_url=person.linkedin_url,
            email=person.email,
            notes=f"Contact Hunter で発見（出典: {person.source_url or '不明'}）",
        )
        db.add(contact)
        db.flush()
        contact_id = contact.id

    summary = f"担当者を反映: {person.name}"
    if person.title:
        summary += f" / {person.title}"
    if person.department:
        summary += f"（{person.department}・優先度{person.priority}）"
    db.add(
        SalesActivity(
            maker_id=maker.id,
            project_id=project.id,
            contact_id=contact_id,
            kind=ActivityKind.note.value,
            summary=summary[:2000],
        )
    )
    db.commit()
    return maker.id, contact_id
