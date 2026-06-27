"""返信メール AI サポートの業務ロジック。

受信メールを解析し（モック/Claude）、英語の返信案を生成して保存する。返信本文には
email_settings の署名を末尾連結する。Gmail（未設定なら mock）に返信下書きを作成でき、
CRM にも営業履歴を記録する。送信はしない。
"""
from __future__ import annotations

import logging

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai.prompts import SenderContext, append_signature
from app.ai.reply_assistant import (
    DEFAULT_REPLY_TONE,
    IncomingEmail,
    ReplyAssistant,
    ReplyTone,
    get_reply_assistant,
)
from app.email import active_provider_name, get_email_provider
from app.email.providers.base import EmailMessage, EmailProviderError
from app.models.crm import ActivityKind, SalesActivity
from app.models.project import Project
from app.models.reply_assistant import ReplyAssistant as ReplyAssistantRow
from app.models.reply_assistant import ReplyStatus
from app.services import email_settings_service, usage_service

logger = logging.getLogger("reply_assistant")


def _add_activity(db: Session, project: Project, summary: str) -> None:
    """CRM に営業履歴を追加する（メーカー紐づけ時のみ・失敗は無視）。"""
    if not project.maker_id:
        return
    try:
        db.add(
            SalesActivity(
                maker_id=project.maker_id,
                project_id=project.id,
                kind=ActivityKind.email.value,
                summary=summary[:2000],
            )
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001  CRM 連携失敗は本処理を妨げない
        db.rollback()
        logger.warning("crm activity add failed (project=%s): %s", project.id, exc)


def create_reply_assist(
    db: Session,
    project: Project,
    *,
    incoming_subject: str | None,
    incoming_body: str,
    incoming_from: str | None,
    reply_tone: ReplyTone = DEFAULT_REPLY_TONE,
    assistant: ReplyAssistant | None = None,
) -> ReplyAssistantRow:
    """受信メールを解析し返信案を生成・保存する。失敗は status=failed で保存。"""
    assistant = assistant or get_reply_assistant()
    ctx = SenderContext.from_settings(email_settings_service.get_settings(db))

    row = ReplyAssistantRow(
        project_id=project.id,
        maker_id=project.maker_id,
        incoming_subject=incoming_subject,
        incoming_body=incoming_body,
        incoming_from=incoming_from,
        reply_tone=reply_tone.value,
        status=ReplyStatus.draft.value,
        model=assistant.name,
    )
    db.add(row)

    try:
        incoming = IncomingEmail(
            subject=incoming_subject or "",
            body=incoming_body,
            sender=incoming_from or "",
        )
        result = assistant.assist(project, incoming, reply_tone)

        row.detected_language = result.detected_language
        row.japanese_summary = result.japanese_summary
        row.intent = result.intent
        row.sentiment = result.sentiment
        row.key_points = result.key_points or None
        row.requested_actions = result.requested_actions or None
        row.risks_or_cautions = result.risks_or_cautions or None
        row.recommended_next_action = result.recommended_next_action
        # 署名は AI ではなくここで固定テンプレートを末尾連結する
        row.reply_body = append_signature(result.reply_body, ctx)
        subject = result.reply_subject or (incoming_subject or project.title)
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        row.reply_subject = subject
        row.model = result.model or assistant.name
        row.status = ReplyStatus.completed.value

        usage_service.record_usage(
            db,
            kind="reply_assist",
            model=row.model,
            usage=getattr(assistant, "last_usage", None),
            project_id=project.id,
        )
        db.commit()
        db.refresh(row)

        # CRM 連携：返信受信（解析）を記録
        note = "返信受信（AI解析）"
        if row.japanese_summary:
            note += f": {row.japanese_summary}"
        if row.recommended_next_action:
            note += f" / 推奨: {row.recommended_next_action}"
        _add_activity(db, project, note)
    except Exception as exc:  # noqa: BLE001  失敗は failed として保存
        logger.warning("reply assist failed (project=%s): %s", project.id, exc)
        row.status = ReplyStatus.failed.value
        row.error = str(exc)[:4000]
        db.commit()
        db.refresh(row)

    return row


def list_assists(db: Session, project_id: int) -> list[ReplyAssistantRow]:
    stmt = (
        select(ReplyAssistantRow)
        .where(ReplyAssistantRow.project_id == project_id)
        .order_by(desc(ReplyAssistantRow.created_at), desc(ReplyAssistantRow.id))
    )
    return list(db.scalars(stmt))


def get_assist(db: Session, reply_assist_id: int) -> ReplyAssistantRow | None:
    return db.get(ReplyAssistantRow, reply_assist_id)


def create_gmail_draft(
    db: Session, row: ReplyAssistantRow, to: str | None = None
) -> dict:
    """返信案を Gmail（未設定なら mock）に返信下書きとして作成する。

    宛先は to → incoming_from の順。件名は reply_subject。送信はしない。
    結果を gmail_draft_id / gmail_web_link に保存し、CRM にも履歴を残す。
    Returns: 作成結果の dict。
    Raises: ValueError（宛先なし）, EmailProviderError（プロバイダー失敗）
    """
    recipient = (to or "").strip() or (row.incoming_from or "").strip()
    if not recipient:
        raise ValueError(
            "宛先メールアドレスがありません。to を指定するか、差出人を入力してください。"
        )
    if not row.reply_body:
        raise ValueError("返信案がありません。先に返信案を作成してください。")

    provider = get_email_provider()
    result = provider.create_draft(
        EmailMessage(
            to=recipient,
            subject=row.reply_subject or "Re:",
            body=row.reply_body,
        )
    )
    row.gmail_draft_id = result.draft_id
    row.gmail_web_link = result.web_link
    db.commit()
    db.refresh(row)

    project = db.get(Project, row.project_id)
    if project is not None:
        _add_activity(db, project, "返信下書き作成: Gmail返信下書きを作成")

    return {
        "provider": result.provider,
        "draft_id": result.draft_id,
        "status": result.status,
        "to": recipient,
        "web_link": result.web_link,
        "detail": result.detail,
    }


def provider_name() -> str:
    return active_provider_name()
