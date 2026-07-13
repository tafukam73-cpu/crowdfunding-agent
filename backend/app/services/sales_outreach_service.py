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
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.sales_outreach import (
    LANGUAGE_LABELS,
    build_multilang_outreach,
    recommended_language,
)
from app.models.contact_discovery import ContactDiscovery, DiscoveryStatus
from app.models.project import SALES_TARGET_SITES, Project, SalesStatus
from app.models.sales_assessment import SalesAssessment
from app.models.sales_outreach import OutreachStatus, SalesOutreach

logger = logging.getLogger("sales_outreach")

# 営業対象外（既に決着した）営業状況。today-priority から除外する。
_CLOSED_SALES_STATUS = {SalesStatus.won.value, SalesStatus.rejected.value}

# 終端の outreach 状態（生成し直しても状態は変えない）。
_TERMINAL_OUTREACH = {OutreachStatus.contract.value, OutreachStatus.lost.value}


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
        .where(Project.source_site.in_(values))
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
    }


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
