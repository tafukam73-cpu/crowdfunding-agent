"""メール下書きのプロバイダー連携（Gmail 等）。

生成済みの EmailDraft を、設定されたプロバイダー（未設定なら mock）に
「下書き」として作成する。送信はしない。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.email import get_email_provider
from app.email.providers.base import DraftResult, EmailMessage
from app.models.crm import Contact
from app.models.email_draft import EmailDraft
from app.models.project import Project


def get_draft(db: Session, draft_id: int) -> EmailDraft | None:
    return db.get(EmailDraft, draft_id)


def resolve_recipient(db: Session, draft: EmailDraft, to: str | None) -> str | None:
    """宛先メールアドレスを決定する。

    優先順位：明示指定 to → 紐づくメーカー担当者のメール → 案件の連絡先候補。
    """
    if to and to.strip():
        return to.strip()

    project = db.get(Project, draft.project_id)
    if project is None:
        return None

    if project.maker_id:
        contact = db.scalar(
            select(Contact)
            .where(Contact.maker_id == project.maker_id, Contact.email.is_not(None))
            .order_by(Contact.id)
            .limit(1)
        )
        if contact and contact.email:
            return contact.email.strip()

    if project.contact_info and "@" in project.contact_info:
        return project.contact_info.strip()

    return None


def create_provider_draft(
    db: Session, draft: EmailDraft, to: str | None = None
) -> tuple[DraftResult, str]:
    """プロバイダーに下書きを作成し、EmailDraft に記録する。

    Returns: (結果, 解決した宛先)
    Raises: ValueError（宛先なし）, EmailProviderError（プロバイダー失敗）
    """
    recipient = resolve_recipient(db, draft, to)
    if not recipient:
        raise ValueError(
            "宛先メールアドレスがありません。to を指定するか、"
            "メーカー担当者にメールアドレスを登録してください。"
        )

    provider = get_email_provider()
    result = provider.create_draft(
        EmailMessage(to=recipient, subject=draft.subject, body=draft.body)
    )

    draft.provider = result.provider
    draft.provider_draft_id = result.draft_id
    db.commit()
    db.refresh(draft)
    return result, recipient
