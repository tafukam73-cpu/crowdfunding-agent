"""営業実行パイプライン（sales_outreach）の業務ロジック。

Discovery → Promote → Contact Intelligence → 営業実行 を一本化する最終段。

- today_priority: 「今日営業する案件」を優先度順に返す（読み取り専用・バッチ取得）。
  Contact Intelligence 未完了の案件は除外する。GET 内で重い処理は行わない。
- request_generation: 4 言語の営業メール下書きを **背景ジョブ** で生成する。
  同一案件の下書きは 1 本のみ（重複作成防止）。外部 Claude 呼び出しは POST では行わない。
- run_generation: 背景ジョブ本体（多言語生成 → sales_outreach へ保存 → CRM 反映）。

優先度の重み（要件 3）：
  日本市場適性 / 独占販売可能性 / Makuake 適性 / email 取得 / maker 取得 / 新規案件。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.sales_outreach import (
    LANGUAGE_LABELS,
    build_multilang_outreach,
    recommended_language,
)
from app.models.contact_discovery import ContactDiscovery, DiscoveryStatus
from app.models.project import (
    SALES_TARGET_SITES,
    Project,
    SalesStatus,
    not_archived_clause,
)
from app.models.sales_assessment import SalesAssessment
from app.models.sales_outreach import OutreachStatus, SalesOutreach
from app.services import campaign_url as campaign_url_mod

logger = logging.getLogger("sales_outreach")

# 営業対象外（既に決着した）営業状況。today-priority から除外する。
_CLOSED_SALES_STATUS = {SalesStatus.won.value, SalesStatus.rejected.value}

# 終端の outreach 状態（生成し直しても状態は変えない）。
_TERMINAL_OUTREACH = {OutreachStatus.contract.value, OutreachStatus.lost.value}

# フォローアップ対象外の状態（返信あり・商談中・終端）。
_NO_FOLLOWUP_STATUS = {
    OutreachStatus.replied.value,
    OutreachStatus.negotiating.value,
    OutreachStatus.contract.value,
    OutreachStatus.lost.value,
}

# 送信後ワークフローのパラメータ。
FOLLOWUP_BUSINESS_DAYS = 5   # 送信/前回フォローの 5 営業日後にフォロー期日を置く
MAX_FOLLOWUPS = 2            # フォローアップは最大 2 回まで


def _now() -> datetime:
    return datetime.now(timezone.utc)


def add_business_days(start: datetime, n: int) -> datetime:
    """start から n 営業日後（土日をスキップ）。時刻は保持する。"""
    d = start
    added = 0
    while added < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5:  # 月(0)〜金(4)
            added += 1
    return d


# ---------------- 優先度スコア（DB 非依存の純粋関数） ----------------
def compute_priority(
    *,
    japan_market_fit: int | None,
    exclusivity: int | None,
    makuake_fit: int | None,
    has_email: bool,
    has_maker: bool,
    is_new: bool,
) -> tuple[int, list[str]]:
    """営業実行優先度（0〜100）と理由を算出する。

    重み: 適性 3 スコアで最大 80、到達性(email)+10、maker 取得+5、新規案件+5。
    適性が未評価（None）なら適性由来の点は 0（＝低優先）。
    """
    jm = max(0, min(100, japan_market_fit or 0))
    ex = max(0, min(100, exclusivity or 0))
    mk = max(0, min(100, makuake_fit or 0))

    score = 0.30 * jm + 0.30 * ex + 0.20 * mk  # 最大 80
    reasons: list[str] = []
    if jm >= 60:
        reasons.append(f"日本市場適性が高い（{jm}）")
    if ex >= 60:
        reasons.append(f"独占販売の可能性（{ex}）")
    if mk >= 60:
        reasons.append(f"Makuake 適性が高い（{mk}）")

    if has_email:
        score += 10
        reasons.append("メール連絡先あり")
    if has_maker:
        score += 5
        reasons.append("メーカー特定済み")
    if is_new:
        score += 5
        reasons.append("新規案件（未営業）")

    if not reasons:
        reasons.append(f"総合適性 日本{jm}/独占{ex}/Makuake{mk}")
    return int(max(0, min(100, round(score)))), reasons


# ---------------- 連絡先・CI 完了の判定 ----------------
def _contact_flags(cd: ContactDiscovery | None) -> dict:
    """最新の Contact Discovery から到達性フラグを作る。"""
    if cd is None:
        return {"ci_completed": False, "has_email": False, "has_form": False}
    return {
        "ci_completed": cd.status == DiscoveryStatus.completed.value,
        "has_email": bool(cd.primary_email or cd.discovered_emails),
        "has_form": bool(cd.primary_contact_form_url or cd.discovered_forms),
    }


def _recommended_action(
    *, contact_ready: bool, has_draft: bool, has_channel: bool
) -> str:
    if has_draft:
        return "open_draft"        # 生成済み下書きを開いて送信する
    if contact_ready:
        return "generate_email"    # 4 言語メールを生成する
    if has_channel:
        return "generate_email"
    return "find_contact"          # 連絡先探索の深掘りが必要


# ---------------- バッチ取得（N+1 回避） ----------------
def _batch_latest(db: Session, model, project_ids: list[int]) -> dict:
    """各 project_id の最新（最大 id）行を 1 クエリで取得して map で返す。"""
    if not project_ids:
        return {}
    sub = (
        select(model.project_id, func.max(model.id).label("mid"))
        .where(model.project_id.in_(project_ids))
        .group_by(model.project_id)
        .subquery()
    )
    rows = db.scalars(select(model).join(sub, model.id == sub.c.mid))
    return {r.project_id: r for r in rows}


def _outreach_map(db: Session, project_ids: list[int]) -> dict:
    if not project_ids:
        return {}
    rows = db.scalars(
        select(SalesOutreach).where(SalesOutreach.project_id.in_(project_ids))
    )
    return {r.project_id: r for r in rows}


# ---------------- 今日営業する案件 ----------------
def today_priority(db: Session, *, limit: int = 20, scan_limit: int = 200) -> list[dict]:
    """「今日営業する案件」を優先度順で返す（読み取り専用・バッチ取得）。

    - Contact Intelligence 未完了（完了した Contact Discovery が無い）案件は除外する。
    - 決着済み（成約 / 見送り）の営業状況は除外する。
    - 未評価（Assessment 無し）は適性 0 として低優先に回る。
    外部 HTTP・Claude・保存・ジョブ起動は一切行わない（GET 内で重い処理禁止）。
    """
    values = [s.value for s in SALES_TARGET_SITES]
    stmt = (
        select(Project)
        .where(Project.source_site.in_(values), not_archived_clause())
        .order_by(Project.latest_score.desc().nullslast(), Project.updated_at.desc())
        .limit(scan_limit)
    )
    projects = list(db.scalars(stmt))
    ids = [p.id for p in projects]

    sa_map = _batch_latest(db, SalesAssessment, ids)
    cd_map = _batch_latest(db, ContactDiscovery, ids)
    out_map = _outreach_map(db, ids)

    items: list[dict] = []
    for p in projects:
        if p.sales_status in _CLOSED_SALES_STATUS:
            continue
        flags = _contact_flags(cd_map.get(p.id))
        # Contact Intelligence 未完了案件は除外（要件 9）。
        if not flags["ci_completed"]:
            continue

        sa = sa_map.get(p.id)
        d = (sa.details_json or {}) if sa is not None else {}
        jm = (d.get("japan_market_fit") or {}).get("score")
        ex = (d.get("exclusivity") or {}).get("score")
        mk = (d.get("makuake_fit") or {}).get("score")
        has_email = flags["has_email"]
        has_channel = flags["has_email"] or flags["has_form"]
        is_new = p.sales_status == SalesStatus.not_started.value

        score, reasons = compute_priority(
            japan_market_fit=jm, exclusivity=ex, makuake_fit=mk,
            has_email=has_email, has_maker=bool(p.maker_name), is_new=is_new,
        )
        outreach = out_map.get(p.id)
        has_draft = outreach is not None and outreach.generated_at is not None
        contact_ready = has_channel
        action = _recommended_action(
            contact_ready=contact_ready, has_draft=has_draft, has_channel=has_channel
        )
        items.append({
            "project_id": p.id,
            "title": p.title,
            "source_site": p.source_site,
            "score": score,
            "reasons": reasons,
            "contact_ready": contact_ready,
            "recommended_action": action,
            "outreach_status": outreach.outreach_status if outreach else None,
            "recommended_language": recommended_language(p),
            "evaluated": sa is not None,
        })

    items.sort(key=lambda c: c["score"], reverse=True)
    return items[:limit]


# ---------------- 下書き行の upsert（重複防止） ----------------
def _priority_for_project(db: Session, project: Project) -> int:
    sa = db.scalar(
        select(SalesAssessment)
        .where(SalesAssessment.project_id == project.id)
        .order_by(SalesAssessment.id.desc())
        .limit(1)
    )
    d = (sa.details_json or {}) if sa is not None else {}
    cd = db.scalar(
        select(ContactDiscovery)
        .where(ContactDiscovery.project_id == project.id)
        .order_by(ContactDiscovery.id.desc())
        .limit(1)
    )
    flags = _contact_flags(cd)
    score, _ = compute_priority(
        japan_market_fit=(d.get("japan_market_fit") or {}).get("score"),
        exclusivity=(d.get("exclusivity") or {}).get("score"),
        makuake_fit=(d.get("makuake_fit") or {}).get("score"),
        has_email=flags["has_email"],
        has_maker=bool(project.maker_name),
        is_new=project.sales_status == SalesStatus.not_started.value,
    )
    return score


def _recipient_email(db: Session, project_id: int) -> str | None:
    cd = db.scalar(
        select(ContactDiscovery)
        .where(ContactDiscovery.project_id == project_id)
        .order_by(ContactDiscovery.id.desc())
        .limit(1)
    )
    if cd is None:
        return None
    if cd.primary_email:
        return cd.primary_email
    emails = cd.discovered_emails or []
    if emails:
        first = emails[0]
        return first.get("email") if isinstance(first, dict) else str(first)
    return None


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def serialize(db: Session, row: SalesOutreach) -> dict:
    """SalesOutreach を API 出力 dict に変換する（既存 Gmail compose URL を付与）。"""
    from app.ai.followup import gmail_compose_url

    recipient = _recipient_email(db, row.project_id)
    compose_url = None
    if row.generated_subject and row.generated_body:
        # 既存の Gmail compose を再利用（新規実装しない・要件 7）。
        compose_url = gmail_compose_url(
            recipient, row.generated_subject, row.generated_body
        )
    return {
        "id": row.id,
        "project_id": row.project_id,
        "outreach_status": row.outreach_status,
        "priority_score": row.priority_score,
        "generated_subject": row.generated_subject,
        "generated_body": row.generated_body,
        "generated_language": row.generated_language,
        "generated_variants": row.generated_variants,
        "generated_at": _iso(row.generated_at),
        "sent_at": _iso(row.sent_at),
        "replied_at": _iso(row.replied_at),
        "last_activity_at": _iso(row.last_activity_at),
        "notes": row.notes,
        "gmail_compose_url": compose_url,
        "recipient": recipient,
        # --- 送信後ワークフロー（0045） ---
        "recipient_email": row.recipient_email,
        "sent_subject": row.sent_subject,
        "sent_body_snapshot": row.sent_body_snapshot,
        "sent_language": row.sent_language,
        "followup_due_at": _iso(row.followup_due_at),
        "followup_count": row.followup_count,
        "last_followup_at": _iso(row.last_followup_at),
        "followups_remaining": max(0, MAX_FOLLOWUPS - (row.followup_count or 0)),
        "reply_intent": row.reply_intent,
        "reply_summary": row.reply_summary,
        "reply_confidence": row.reply_confidence,
        "last_reply_at": _iso(row.last_reply_at),
        "user_edited": row.user_edited,
        "edited_at": _iso(row.edited_at),
    }


def _add_timeline(db: Session, project: Project, summary: str) -> None:
    """CRM の営業履歴タイムライン（SalesActivity）へイベントを 1 件追加する。

    起点は「メーカー」なので maker 未紐づけ案件では何もしない。失敗しても本処理は
    妨げない（送信・フォロー・返信登録の記録は best-effort）。
    """
    from app.models.crm import ActivityKind, SalesActivity

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
    except Exception as exc:  # noqa: BLE001  タイムライン記録失敗は本処理を止めない
        db.rollback()
        logger.warning("outreach timeline add failed (project=%s): %s", project.id, exc)


def get_by_project(db: Session, project_id: int) -> SalesOutreach | None:
    return db.scalar(
        select(SalesOutreach).where(SalesOutreach.project_id == project_id)
    )


def get_or_create(db: Session, project: Project) -> SalesOutreach:
    """案件の営業アウトリーチ行を取得（無ければ draft で作成）。1 案件 1 本。"""
    row = get_by_project(db, project.id)
    if row is None:
        row = SalesOutreach(
            project_id=project.id,
            outreach_status=OutreachStatus.draft.value,
            priority_score=_priority_for_project(db, project),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


# ---------------- 背景ジョブ本体：多言語生成 → 保存 → CRM 反映 ----------------
def run_generation(db: Session, project: Project, progress_cb=None) -> SalesOutreach:
    """4 言語の営業メールを生成して sales_outreach に保存し、CRM に反映する。

    Contact Intelligence ジョブ（outreach_generation）から呼ばれる。外部 Claude を
    呼ぶ場合もこの背景ジョブ内で行う（同期 POST/GET では呼ばない）。
    """
    from app.ai.prompts import SenderContext
    from app.services import (
        company_research_service,
        email_settings_service,
        japan_sales_service,
    )

    row = get_or_create(db, project)
    if progress_cb:
        progress_cb("営業メールを4言語で生成中（英語/韓国語/中国語/日本語）", 0.2)

    ctx = SenderContext.from_settings(email_settings_service.get_settings(db))
    research = company_research_service.to_context(
        company_research_service.get_latest_completed(db, project.id)
    )
    japan_sales = japan_sales_service.to_email_context(
        japan_sales_service.get_latest_completed(db, project.id)
    )
    result = build_multilang_outreach(
        project, ctx, research=research, japan_sales=japan_sales
    )
    rec = result["recommended_language"]
    variant = result["variants"][rec]

    # ユーザーが編集済みの下書きは AI 再生成で勝手に上書きしない（要件：編集保護）。
    # 生成本文には触れず、CRM 反映だけ行って終える。
    if row.user_edited and row.generated_subject and row.generated_body:
        if progress_cb:
            progress_cb("ユーザー編集済みの下書きを保持（本文は上書きしません）", 0.85)
        row.last_activity_at = _now()
        row.priority_score = _priority_for_project(db, project)
        db.commit()
        db.refresh(row)
        _reflect_crm(db, project, row, row.generated_language or rec)
        return row

    row.generated_variants = result["variants"]
    row.generated_language = rec
    row.generated_subject = variant["subject"]
    row.generated_body = variant["body"]
    row.generated_at = _now()
    row.last_activity_at = _now()
    row.priority_score = _priority_for_project(db, project)
    row.notes = result["japanese_summary"]
    # 生成し直しても終端状態（成約/失注）は動かさない。それ以外は draft に戻す。
    if row.outreach_status not in _TERMINAL_OUTREACH:
        row.outreach_status = OutreachStatus.draft.value
    db.commit()
    db.refresh(row)

    if progress_cb:
        progress_cb("CRM（営業案件）へ反映中", 0.85)
    _reflect_crm(db, project, row, rec)
    return row


def _reflect_crm(
    db: Session, project: Project, outreach: SalesOutreach, language: str
) -> None:
    """メール生成を CRM（sales_opportunities）へ反映する（要件 8）。

    最新の Contact Discovery があれば営業案件を冪等に作成し、次アクション/メモを
    更新する。連絡先探索が無ければ何もしない。失敗しても生成本体は妨げない。
    """
    from app.services import sales_opportunity_service as sos

    try:
        cd = db.scalar(
            select(ContactDiscovery)
            .where(ContactDiscovery.project_id == project.id)
            .order_by(ContactDiscovery.id.desc())
            .limit(1)
        )
        if cd is None:
            return
        opp, _created = sos.create_from_contact_discovery(db, cd.id)
        lang_label = LANGUAGE_LABELS.get(language, language)
        sos.update(
            db,
            opp.id,
            next_action=f"生成済み営業メール（{lang_label}）を送信する",
            notes=(
                f"営業実行パイプラインで {lang_label} を推奨言語として"
                f"4 言語の下書きを生成（outreach #{outreach.id}）"
            ),
        )
    except Exception as exc:  # noqa: BLE001  CRM 反映失敗は生成本体を止めない
        logger.warning("outreach CRM reflect failed (project=%s): %s", project.id, exc)
        db.rollback()


# ---------------- 生成リクエスト（背景ジョブ起動・重複防止） ----------------
def request_generation(db: Session, project: Project, *, runner=None) -> dict:
    """営業メール生成を背景ジョブで起動する（重複ジョブ・重複下書きを作らない）。

    Returns: {outreach, job_id, job_status, created, duplicate}
    """
    from app.models.contact_intelligence_job import CIJobType
    from app.services import contact_intelligence_service as ci

    row = get_or_create(db, project)

    active = ci.find_active(db, project.id, CIJobType.outreach_generation.value)
    if active is not None:
        # 既に生成ジョブが動作中：重複ジョブを作らない。
        return {
            "outreach": row,
            "job_id": active.id,
            "job_status": active.status,
            "created": False,
            "duplicate": True,
        }
    job, _from_cache = ci.create_job(
        db, project, CIJobType.outreach_generation.value, force=True, runner=runner
    )
    return {
        "outreach": row,
        "job_id": job.id,
        "job_status": job.status,
        "created": True,
        "duplicate": False,
    }


# ================= 送信後ワークフロー（0045） =================

# ---------------- 1. 下書きの編集・保存（同期・高速・AI 非依存） ----------------
def update_draft(
    db: Session,
    project: Project,
    *,
    subject: str | None = None,
    body: str | None = None,
    language: str | None = None,
) -> SalesOutreach:
    """ユーザーによる下書き編集を保存する（同期・外部呼び出しなし）。

    - 編集された言語のバリアント（generated_variants[lang]）と採用中の
      generated_subject/body を更新する。
    - user_edited=True・edited_at を立て、以後の AI 再生成から保護する。
    """
    row = get_or_create(db, project)
    lang = language or row.generated_language or "en"

    variants = dict(row.generated_variants or {})
    v = dict(variants.get(lang) or {})
    if subject is not None:
        v["subject"] = subject
    if body is not None:
        v["body"] = body
    if v:
        variants[lang] = v
        row.generated_variants = variants

    # 採用中の言語を編集した場合は表示用の generated_* も更新する。
    row.generated_language = lang
    if subject is not None:
        row.generated_subject = subject
    if body is not None:
        row.generated_body = body

    row.user_edited = True
    row.edited_at = _now()
    row.last_activity_at = _now()
    db.commit()
    db.refresh(row)
    return row


# ---------------- 2. 送信済みとして記録（ユーザー操作のみ・冪等） ----------------
def mark_sent(
    db: Session,
    project: Project,
    *,
    language: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    recipient: str | None = None,
) -> dict:
    """「送信済みとして記録」する。実メールは送らない（Gmail は別途ユーザーが送る）。

    - 送信時の宛先・件名・本文・言語をスナップショットとして凍結する。
    - outreach_status を sent にし、送信の 5 営業日後を followup_due_at に設定する。
    - 二重呼び出しは冪等（既に送信済みならスナップショットを書き換えない）。
    - 終端状態（成約/失注）は保護する。
    Returns: {outreach, already_sent}
    """
    row = get_or_create(db, project)

    # 終端状態は送信記録で動かさない。
    if row.outreach_status in _TERMINAL_OUTREACH:
        return {"outreach": row, "already_sent": True}

    # 既に送信済み（draft 以外＝sent/opened/replied/...）は冪等：再スナップショットしない。
    if row.sent_at is not None or row.outreach_status != OutreachStatus.draft.value:
        return {"outreach": row, "already_sent": True}

    lang = language or row.generated_language or "en"
    variant = (row.generated_variants or {}).get(lang) or {}
    snap_subject = subject or variant.get("subject") or row.generated_subject
    snap_body = body or variant.get("body") or row.generated_body
    snap_recipient = recipient or _recipient_email(db, project.id)

    now = _now()
    row.recipient_email = snap_recipient
    row.sent_subject = snap_subject
    row.sent_body_snapshot = snap_body
    row.sent_language = lang
    row.sent_at = now
    row.outreach_status = OutreachStatus.sent.value
    row.followup_due_at = add_business_days(now, FOLLOWUP_BUSINESS_DAYS)
    row.last_activity_at = now
    db.commit()
    db.refresh(row)

    lang_label = LANGUAGE_LABELS.get(lang, lang)
    _reflect_crm_status(
        db, project, next_action="返信を待つ（5 営業日後にフォロー）",
        note=f"営業メール（{lang_label}）を送信済みとして記録（outreach #{row.id}）",
    )
    _add_timeline(
        db, project,
        f"営業メール送信（{lang_label}）: 宛先 {snap_recipient or '不明'} / "
        f"件名「{(snap_subject or '')[:60]}」",
    )
    return {"outreach": row, "already_sent": False}


def _reflect_crm_status(
    db: Session, project: Project, *, next_action: str, note: str
) -> None:
    """CRM（sales_opportunities）の next_action/notes を冪等に更新する（best-effort）。"""
    from app.services import sales_opportunity_service as sos

    try:
        cd = db.scalar(
            select(ContactDiscovery)
            .where(ContactDiscovery.project_id == project.id)
            .order_by(ContactDiscovery.id.desc())
            .limit(1)
        )
        if cd is None:
            return
        opp, _created = sos.create_from_contact_discovery(db, cd.id)
        sos.update(db, opp.id, next_action=next_action, notes=note)
    except Exception as exc:  # noqa: BLE001  CRM 反映失敗は本処理を止めない
        logger.warning("outreach CRM status reflect failed (project=%s): %s", project.id, exc)
        db.rollback()


# ---------------- 3. フォローアップ（適格判定・背景ジョブ・重複防止） ----------------
def followup_eligibility(row: SalesOutreach) -> tuple[bool, str | None]:
    """フォローアップ生成の可否を判定する（純粋関数）。

    - 送信済み（sent）のみ対象。draft/返信あり/商談中/成約/失注は対象外。
    - フォローアップは最大 MAX_FOLLOWUPS 回まで。
    """
    if row.outreach_status in _NO_FOLLOWUP_STATUS:
        return False, "返信あり・商談中・成約・失注はフォロー対象外です"
    if row.outreach_status != OutreachStatus.sent.value:
        return False, "未送信のためフォローできません（先に送信済みとして記録してください）"
    if (row.followup_count or 0) >= MAX_FOLLOWUPS:
        return False, f"フォローアップは最大 {MAX_FOLLOWUPS} 回までです"
    return True, None


def run_followup_generation(db: Session, project: Project, progress_cb=None) -> SalesOutreach:
    """フォローアップメールを 1 通生成し、下書き（generated_*）を差し替える（背景ジョブ本体）。

    送信時の言語（sent_language）で生成し、followup_count を +1、次回フォロー期日を
    5 営業日後に更新する。outreach_status は sent のまま（引き続き返信待ち）。
    """
    from app.ai.prompts import SenderContext
    from app.ai.sales_outreach import build_followup_outreach
    from app.services import email_settings_service

    row = get_or_create(db, project)
    ok, reason = followup_eligibility(row)
    if not ok:
        # ジョブ実行時点で不適格になった場合は何もしない（保護）。
        logger.info("followup skipped (project=%s): %s", project.id, reason)
        return row

    if progress_cb:
        progress_cb("フォローアップメールを生成中", 0.3)

    lang = row.sent_language or row.generated_language or "en"
    n = (row.followup_count or 0) + 1
    ctx = SenderContext.from_settings(email_settings_service.get_settings(db))
    fu = build_followup_outreach(project, ctx, language=lang, followup_number=n)

    now = _now()
    variants = dict(row.generated_variants or {})
    v = dict(variants.get(lang) or {})
    v["subject"] = fu["subject"]
    v["body"] = fu["body"]
    variants[lang] = v
    row.generated_variants = variants
    row.generated_language = lang
    row.generated_subject = fu["subject"]
    row.generated_body = fu["body"]
    row.followup_count = n
    row.last_followup_at = now
    row.followup_due_at = add_business_days(now, FOLLOWUP_BUSINESS_DAYS)
    row.last_activity_at = now
    # フォローアップは新規 AI 生成物なので、以後の再フォロー生成を妨げないよう
    # user_edited は解除する（初回下書きの編集保護とは別軸）。
    row.user_edited = False
    db.commit()
    db.refresh(row)

    if progress_cb:
        progress_cb("CRM・タイムラインへ反映中", 0.85)
    lang_label = LANGUAGE_LABELS.get(lang, lang)
    _reflect_crm_status(
        db, project,
        next_action=f"フォローアップ #{n}（{lang_label}）を送信する",
        note=f"フォローアップ #{n} を生成（outreach #{row.id}）",
    )
    _add_timeline(db, project, f"フォローアップ #{n} 生成（{lang_label}）")
    return row


def request_followup_generation(db: Session, project: Project, *, runner=None) -> dict:
    """フォローアップ生成を背景ジョブで起動する（適格判定・重複ジョブ防止）。

    Returns: {outreach, job_id, job_status, created, duplicate, eligible, reason}
    """
    from app.models.contact_intelligence_job import CIJobType
    from app.services import contact_intelligence_service as ci

    row = get_or_create(db, project)
    ok, reason = followup_eligibility(row)
    if not ok:
        return {
            "outreach": row, "job_id": None, "job_status": None,
            "created": False, "duplicate": False, "eligible": False, "reason": reason,
        }

    active = ci.find_active(db, project.id, CIJobType.followup_generation.value)
    if active is not None:
        # 既にフォローアップ生成ジョブが動作中：重複ジョブを作らない。
        return {
            "outreach": row, "job_id": active.id, "job_status": active.status,
            "created": False, "duplicate": True, "eligible": True, "reason": None,
        }
    job, _from_cache = ci.create_job(
        db, project, CIJobType.followup_generation.value, force=True, runner=runner
    )
    return {
        "outreach": row, "job_id": job.id, "job_status": job.status,
        "created": True, "duplicate": False, "eligible": True, "reason": None,
    }


# ---------------- 4. 返信の手動貼り付け（preview は非保存 / confirm で保存） ----------------
def _reply_confidence(intent: str, sentiment: str) -> str:
    """解析の確度ラベル（high / medium / low）を決める（決定的）。"""
    if intent == "unclear":
        return "low"
    if sentiment == "neutral":
        return "medium"
    return "high"


def _analyze_reply(project: Project, db: Session, *, incoming_subject, incoming_body,
                   incoming_from, assistant=None) -> dict:
    """受信返信を解析して dict を返す（DB は一切更新しない）。

    assistant を渡すと差し替え可能（テストは MockReplyAssistant を注入）。既定は
    設定に応じたエンジン（未設定ならモック）。外部 Claude 呼び出しがあっても DB
    トランザクションは開かない（この関数内で commit しない）。
    """
    from app.ai.reply_assistant import (
        DEFAULT_REPLY_TONE,
        IncomingEmail,
        get_reply_assistant,
    )

    assistant = assistant or get_reply_assistant()
    incoming = IncomingEmail(
        subject=incoming_subject or "", body=incoming_body or "",
        sender=incoming_from or "",
    )
    result = assistant.assist(project, incoming, DEFAULT_REPLY_TONE)
    confidence = _reply_confidence(result.intent, result.sentiment)
    return {
        "intent": result.intent,
        "sentiment": result.sentiment,
        "detected_language": result.detected_language,
        "summary": result.japanese_summary,
        "confidence": confidence,
        "key_points": result.key_points or [],
        "requested_actions": result.requested_actions or [],
        "recommended_next_action": result.recommended_next_action,
        "model": result.model,
    }


def reply_preview(db: Session, project: Project, *, incoming_subject=None,
                  incoming_body="", incoming_from=None, assistant=None) -> dict:
    """貼り付けた返信を解析してプレビューを返す（DB 非更新・冪等）。"""
    return _analyze_reply(
        project, db, incoming_subject=incoming_subject, incoming_body=incoming_body,
        incoming_from=incoming_from, assistant=assistant,
    )


def reply_confirm(db: Session, project: Project, *, incoming_subject=None,
                  incoming_body="", incoming_from=None, assistant=None) -> dict:
    """返信を確定登録する。解析結果を保存し、状態を「返信あり」に遷移させる。

    - 先に解析（DB 非更新）してから、短いトランザクションで 1 度だけ保存する
      （idle in transaction を作らない）。
    - 終端状態（成約/失注）は保護する。
    Returns: {outreach, analysis}
    """
    row = get_or_create(db, project)
    analysis = _analyze_reply(
        project, db, incoming_subject=incoming_subject, incoming_body=incoming_body,
        incoming_from=incoming_from, assistant=assistant,
    )

    now = _now()
    row.reply_intent = analysis["intent"]
    row.reply_summary = analysis["summary"]
    row.reply_confidence = analysis["confidence"]
    row.last_reply_at = now
    row.replied_at = row.replied_at or now
    row.last_activity_at = now
    # 終端（成約/失注）は保護。それ以外は「返信あり」に遷移（フォロー対象から外れる）。
    if row.outreach_status not in _TERMINAL_OUTREACH:
        row.outreach_status = OutreachStatus.replied.value
    db.commit()
    db.refresh(row)

    intent_label = _INTENT_LABELS.get(analysis["intent"], analysis["intent"])
    _reflect_crm_status(
        db, project,
        next_action=analysis.get("recommended_next_action") or "返信内容を確認して対応する",
        note=f"返信を登録（意図: {intent_label} / 確度: {analysis['confidence']}）",
    )
    _add_timeline(
        db, project,
        f"返信を登録（意図: {intent_label} / {analysis['confidence']}）: "
        f"{(analysis['summary'] or '')[:120]}",
    )
    return {"outreach": row, "analysis": analysis}


_INTENT_LABELS = {
    "interested": "前向き",
    "needs_more_info": "追加情報要望",
    "asks_terms": "条件確認",
    "requests_call": "面談希望",
    "not_interested": "見送り",
    "already_has_distributor": "既存代理店あり",
    "unclear": "意図不明",
}


# ---------------- 5. 送信後の実行タスク抽出（読み取り専用） ----------------
def execution_tasks(db: Session, *, limit: int = 50) -> dict:
    """送信後の「今日フォロー / 期限超過 / 返信対応 / 返信待ち」を抽出する。

    読み取り専用・バッチ取得。外部 HTTP・Claude・保存・ジョブ起動は一切行わない
    （GET から呼ばれる）。
    Returns: {follow_today, overdue, replied, awaiting_reply}
    """
    now = _now()
    # 送信後（sent / replied / negotiating）の outreach を対象に集める。
    rows = list(
        db.scalars(
            select(SalesOutreach)
            .where(SalesOutreach.sent_at.isnot(None))
            .order_by(SalesOutreach.followup_due_at.asc().nullslast())
        )
    )
    project_ids = [r.project_id for r in rows]
    # 営業対象外（除外済み）案件は送信後フォローの一覧にも出さない。
    proj_map = {
        p.id: p
        for p in db.scalars(
            select(Project).where(
                Project.id.in_(project_ids), not_archived_clause()
            )
        )
    } if project_ids else {}

    follow_today: list[dict] = []
    overdue: list[dict] = []
    replied: list[dict] = []
    awaiting: list[dict] = []

    for r in rows:
        p = proj_map.get(r.project_id)
        # 営業対象外（proj_map から除外済み）はスキップする。
        if p is None:
            continue
        item = _execution_item(db, r, p, now)
        status = r.outreach_status
        if status in (OutreachStatus.replied.value, OutreachStatus.negotiating.value):
            replied.append(item)
            continue
        if status != OutreachStatus.sent.value:
            continue
        # 送信済みで返信なし。
        eligible = (r.followup_count or 0) < MAX_FOLLOWUPS
        due = _aware(r.followup_due_at)
        if eligible and due is not None and due <= now:
            follow_today.append(item)
            if due < _start_of_day(now):
                overdue.append(item)
        else:
            awaiting.append(item)

    return {
        "follow_today": follow_today[:limit],
        "overdue": overdue[:limit],
        "replied": replied[:limit],
        "awaiting_reply": awaiting[:limit],
    }


def _aware(dt: datetime | None) -> datetime | None:
    """naive な datetime（SQLite 等）を UTC aware に正規化する（Postgres は素通り）。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _start_of_day(dt: datetime) -> datetime:
    dt = _aware(dt)
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _execution_item(db: Session, r: SalesOutreach, project: Project | None, now: datetime) -> dict:
    due = _aware(r.followup_due_at)
    days_overdue = None
    if due is not None:
        delta = (_start_of_day(now) - _start_of_day(due)).days
        days_overdue = max(0, delta)
    return {
        "project_id": r.project_id,
        "title": project.title if project else f"#{r.project_id}",
        "source_site": project.source_site if project else None,
        **(
            campaign_url_mod.url_state(project)
            if project is not None
            else {"campaign_url": None, "campaign_url_missing": True,
                  "campaign_url_missing_reason": "no_source_url",
                  "official_site_url": None}
        ),
        "outreach_status": r.outreach_status,
        "recipient": r.recipient_email,
        "sent_at": _iso(r.sent_at),
        "sent_language": r.sent_language,
        "followup_count": r.followup_count,
        "followups_remaining": max(0, MAX_FOLLOWUPS - (r.followup_count or 0)),
        "followup_due_at": _iso(r.followup_due_at),
        "days_overdue": days_overdue,
        "reply_intent": r.reply_intent,
        "reply_summary": r.reply_summary,
        "reply_confidence": r.reply_confidence,
        "last_reply_at": _iso(r.last_reply_at),
    }
