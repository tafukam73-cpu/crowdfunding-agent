"""営業メール下書きの業務ロジック。

生成器（モック/Claude）で 3 種別の下書きを生成して保存する。
自動送信はしない。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import get_email_generator
from app.ai.email_generator import EmailGenerator
from app.ai.outreach import build_outreach_message
from app.ai.prompts import DEFAULT_TONE, EmailTone, SenderContext
from app.models.email_draft import EmailDraft
from app.models.project import Project
from app.services import (
    company_research_service,
    contact_hunter_service,
    email_settings_service,
    japan_sales_service,
    usage_service,
)


def _greeting_contact(db: Session, project_id: int) -> dict | None:
    """冒頭挨拶用の担当者（氏名は first name に短縮）を返す。

    Contact Hunter の最優先担当者を使い、"Dear {名}," を可能にする。氏名が無く部署
    だけのときは "Dear {部署} Team," 用に department を返す。担当者が無ければ None。
    """
    top = contact_hunter_service.get_top_person(db, project_id)
    if top is None:
        return None
    first_name = None
    if top.name:
        parts = top.name.split()
        first_name = parts[0] if parts else None
    return {"name": first_name, "department": top.department}


def generate_drafts(
    db: Session,
    project: Project,
    generator: EmailGenerator | None = None,
    tone: EmailTone = DEFAULT_TONE,
) -> list[EmailDraft]:
    """3 種別の下書きを生成・保存して返す（生成のたびに履歴を追加）。

    tone で文章のトーンを指定（既定 professional）。各下書きは件名候補 3 案と
    日本語要約を保持する。subject には初期選択（候補の先頭）を入れる。
    最新の completed な企業リサーチがあれば、それを反映してより具体的にする。
    最新の completed な日本販売状況チェックがあれば、既存代理店の有無などを反映する。
    """
    generator = generator or get_email_generator()
    # 保存済みメール設定を会社情報・署名コンテキストとして渡す（未登録なら
    # .env フォールバック。設定未登録でも生成は動く）。
    ctx = SenderContext.from_settings(email_settings_service.get_settings(db))
    # 企業リサーチ（あれば）をメール生成へ渡す。無ければ従来どおり None。
    research = company_research_service.to_context(
        company_research_service.get_latest_completed(db, project.id)
    )
    # 日本販売状況チェック（あれば）をメール生成へ渡す。無ければ None。
    japan_sales = japan_sales_service.to_email_context(
        japan_sales_service.get_latest_completed(db, project.id)
    )
    # Contact Hunter が担当者を見つけていれば、冒頭挨拶を担当者名/部署にする
    contact = _greeting_contact(db, project.id)
    results = generator.generate(
        project, ctx, tone, research=research, japan_sales=japan_sales, contact=contact
    )

    drafts: list[EmailDraft] = []
    for r in results:
        selected = r.selected_subject or r.subject
        draft = EmailDraft(
            project_id=project.id,
            email_type=r.email_type.value,
            subject=selected,
            body=r.body,
            language=r.language,
            model=r.model,
            subject_options=r.subject_options or [r.subject],
            selected_subject=selected,
            tone=r.tone or tone.value,
            japanese_summary=r.japanese_summary or None,
            personalization_context=r.personalization_context,
            personalized_compliment=r.personalized_compliment or None,
            product_highlights=r.product_highlights or None,
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


def generate_outreach_message(
    db: Session, project: Project, channel: str
) -> dict:
    """メール以外のチャネル（問い合わせフォーム / SNS DM）向けの短文営業文を作る。

    既存の営業メールとは別物で、署名や独占販売権の詳細は入れず要点のみを 300〜600
    文字程度にまとめる。差出人情報（メール設定）と、あれば企業リサーチを反映する。
    生成のみで DB には保存しない（画面でコピーして使う）。
    """
    ctx = SenderContext.from_settings(email_settings_service.get_settings(db))
    research = company_research_service.to_context(
        company_research_service.get_latest_completed(db, project.id)
    )
    return build_outreach_message(project, channel, ctx, research=research)


def select_subject(db: Session, draft: EmailDraft, subject: str) -> EmailDraft:
    """件名を選択して保存する。

    後方互換のため subject も同期更新する（プロバイダー下書き作成は subject を使う
    ため、選択した件名が Gmail 等の下書きに反映される）。候補外の任意文字列も許可。
    """
    cleaned = subject.strip()
    if not cleaned:
        raise ValueError("件名が空です")
    draft.selected_subject = cleaned
    draft.subject = cleaned
    db.commit()
    db.refresh(draft)
    return draft


def list_drafts(db: Session, project_id: int) -> list[EmailDraft]:
    """案件の下書きを新しい順で返す（種別ごとに最新が先頭）。"""
    stmt = (
        select(EmailDraft)
        .where(EmailDraft.project_id == project_id)
        .order_by(EmailDraft.created_at.desc(), EmailDraft.id.desc())
    )
    return list(db.scalars(stmt))
