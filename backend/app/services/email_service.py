"""営業メール下書きの業務ロジック。

生成器（モック/Claude）で 3 種別の下書きを生成して保存する。
自動送信はしない。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import get_email_generator
from app.ai.email_generator import EmailGenerator
from app.models.email_draft import EmailDraft
from app.models.project import Project
from app.services import usage_service


def generate_drafts(
    db: Session, project: Project, generator: EmailGenerator | None = None
) -> list[EmailDraft]:
    """3 種別の下書きを生成・保存して返す（生成のたびに履歴を追加）。"""
    generator = generator or get_email_generator()
    results = generator.generate(project)

    drafts: list[EmailDraft] = []
    for r in results:
        draft = EmailDraft(
            project_id=project.id,
            email_type=r.email_type.value,
            subject=r.subject,
            body=r.body,
            language=r.language,
            model=r.model,
        )
        db.add(draft)
        drafts.append(draft)

    # Claude 実行時のみトークン/コストを記録（3通合計）
    model = results[0].model if results else generator.name
    usage_service.record_usage(
        db,
        kind="email",
        model=model,
        usage=getattr(generator, "last_usage", None),
        project_id=project.id,
    )

    db.commit()
    for d in drafts:
        db.refresh(d)
    return drafts


def list_drafts(db: Session, project_id: int) -> list[EmailDraft]:
    """案件の下書きを新しい順で返す（種別ごとに最新が先頭）。"""
    stmt = (
        select(EmailDraft)
        .where(EmailDraft.project_id == project_id)
        .order_by(EmailDraft.created_at.desc(), EmailDraft.id.desc())
    )
    return list(db.scalars(stmt))
