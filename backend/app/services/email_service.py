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
from app.models.email_draft import EmailDraft, EmailType
from app.models.project import Project, SalesStatus
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


# --- フォローアップメール（2通目・3通目）------------------------------------- #
# 「返信待ち/フォロー済み」として扱う営業状況。フォロー作成後にこの状態へ寄せる。
_FOLLOWUP_SETTABLE_FROM = (
    SalesStatus.contacted.value,
    SalesStatus.awaiting_reply.value,
    SalesStatus.ready.value,
    SalesStatus.not_started.value,
)


def _record_followup_activity(
    db: Session, project: Project, stage_label: str, days: int
) -> None:
    """フォローアップ作成を営業活動タイムライン（SalesActivity）に記録する。

    メーカー未リンクなら案件情報から作成・リンクしてから記録する（失敗は握りつぶす）。
    """
    from app.models.crm import ActivityKind, SalesActivity
    from app.services import crm_service

    if not project.maker_id:
        try:
            crm_service.create_from_project(db, project)
            db.refresh(project)
        except Exception:  # noqa: BLE001  メーカー作成失敗でも本処理は続行
            db.rollback()
    if not project.maker_id:
        return
    try:
        db.add(
            SalesActivity(
                maker_id=project.maker_id,
                project_id=project.id,
                kind=ActivityKind.email.value,
                summary=f"フォローアップメール作成（{stage_label}・最終営業から{days}日）",
            )
        )
        db.commit()
    except Exception:  # noqa: BLE001  タイムライン記録失敗は本処理を妨げない
        db.rollback()


def generate_followup(
    db: Session,
    project: Project,
    *,
    days: int | None = None,
    set_awaiting_reply: bool = True,
    to: str | None = None,
) -> dict:
    """最終営業からの経過日数に応じたフォローアップ下書きを作成する。

    - 3日未満は ValueError（まだフォロー時期でない）。
    - 段階（light/repropose/final）で文面を変え、EmailDraft として保存する。
    - 既存の差出人情報・企業リサーチ・担当者情報を反映する。
    - Gmail 下書きを開く URL（宛先/件名/本文入り）を生成する。
    - 営業活動タイムラインに記録し、必要なら営業状況を「返信待ち」に更新する。

    Returns: draft / stage / stage_label / days_since_last_outreach /
             follow_up_level / gmail_compose_url / recipient / sales_status
    """
    from app.ai import followup as fu
    from app.services import (
        email_delivery_service,
        project_service,
        workflow_service,
    )

    if days is None:
        days = workflow_service.last_outreach_days(db, project)
    stage = fu.stage_for_days(days)
    if stage is None:
        raise ValueError(
            "まだフォロー時期ではありません（最終営業から3日未満）。"
        )

    ctx = SenderContext.from_settings(email_settings_service.get_settings(db))
    research = company_research_service.to_context(
        company_research_service.get_latest_completed(db, project.id)
    )
    contact = _greeting_contact(db, project.id)
    msg = fu.build_followup_message(
        project, stage=stage, days=days, ctx=ctx, research=research, contact=contact
    )

    draft = EmailDraft(
        project_id=project.id,
        email_type=EmailType.followup.value,
        subject=msg["subject"],
        body=msg["body"],
        language="en",
        model="rule-followup-v1",
        subject_options=msg["subject_options"],
        selected_subject=msg["subject"],
        tone=DEFAULT_TONE.value,
        japanese_summary=msg["japanese_summary"],
        personalization_context={
            "followup_stage": stage,
            "days_since_last_outreach": days,
        },
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    recipient = email_delivery_service.resolve_recipient(db, draft, to)
    compose_url = fu.gmail_compose_url(recipient, draft.subject, draft.body)

    # 活動タイムラインに記録
    _record_followup_activity(db, project, msg["stage_label"], days)

    # 営業状況：フォロー後は「返信待ち」に寄せる（返信あり/商談中/契約/見送りは変えない）
    if set_awaiting_reply and project.sales_status in _FOLLOWUP_SETTABLE_FROM:
        project_service.update_sales_status(db, project, SalesStatus.awaiting_reply)

    return {
        "draft": draft,
        "stage": stage,
        "stage_label": msg["stage_label"],
        "days_since_last_outreach": days,
        "follow_up_level": fu.STAGE_TO_LEVEL[stage],
        "gmail_compose_url": compose_url,
        "recipient": recipient,
        "sales_status": project.sales_status,
    }
