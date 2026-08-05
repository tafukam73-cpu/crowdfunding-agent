"""メール下書きのプロバイダー連携（Gmail 等）。

生成済みの EmailDraft を、設定されたプロバイダー（未設定なら mock）に
「下書き」として作成する。送信はしない。

**営業対象除外判定の主関門はここにある。** プロバイダーへ下書きを作るのは
外部（ユーザーの Gmail）へ実際に書き込む唯一の地点なので、
``provider.create_draft()`` を呼ぶ**前に** pre_outreach 判定を必ず通す。
不合格なら ``LeadQualificationBlocked`` を送出し、プロバイダーは呼ばない。
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
) -> tuple[DraftResult, str, dict | None]:
    """プロバイダーに下書きを作成し、EmailDraft に記録する。

    **``provider.create_draft()`` の直前に営業対象除外判定（pre_outreach）を
    必ず実行する**（モードに依らず判定と履歴保存は行う）。

    - **enforce**: ``clear`` 以外はプロバイダーを一切呼ばずに送出する
      （下書き・アウトリーチ・営業状況・タイムラインのいずれも変更しない）
    - **observe**: 不合格でも従来どおり下書きを作り、判定を監査情報として返す

    Returns: ``(結果, 解決した宛先, qualification payload)``
    Raises:
        ValueError: 宛先なし
        EmailProviderError: プロバイダー失敗
        LeadQualificationBlocked: enforce で営業対象判定により止めた場合
    """
    recipient = resolve_recipient(db, draft, to)
    if not recipient:
        raise ValueError(
            "宛先メールアドレスがありません。to を指定するか、"
            "メーカー担当者にメールアドレスを登録してください。"
        )

    # --- 関門：ここから先はプロバイダー（外部 Gmail）へ書き込む ---
    from app.services import outreach_qualification_gate as gate

    project = db.get(Project, draft.project_id)
    if project is None:
        # 案件が引けなければ判定できない。enforce では fail closed で止める。
        payload = gate.safe_payload(persisted=False)
        if gate.is_enforcing():
            raise gate.LeadQualificationBlocked(
                payload, message=gate.MESSAGE_UNAVAILABLE
            )
        qualification = payload
    else:
        qualification = gate.require_clear(db, project)

    provider = get_email_provider()
    result = provider.create_draft(
        EmailMessage(to=recipient, subject=draft.subject, body=draft.body)
    )

    draft.provider = result.provider
    draft.provider_draft_id = result.draft_id
    db.commit()
    db.refresh(draft)
    return result, recipient, qualification
