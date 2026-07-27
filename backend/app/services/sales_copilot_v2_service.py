"""Sales Copilot v2（営業 AI 秘書）— 統合オーケストレーション層。

v1（sales_copilot_service）の判断・次アクション・連絡先シグナルに、v2 で追加した
3 つの適性スコア（日本市場適性 / 独占販売可能性 / Makuake 適性）を重ね合わせ、
**1 案件を『今どう動くか』の統合カード** と **優先度ランキング・ダッシュボード** に
まとめる。既存機能（メール生成・返信解析・優先度・次アクション）は流用する。

方針:
- 既存サービスを壊さず追加（v1 は温存）。判断の中核 combine_v2() は DB 非依存の
  純粋関数として切り出し、fixture でテストする。
- スコアはルールベース（sales_assessment_service）。保存済みが無ければその場で算出。
- v1 の drop/closed/連絡先ロジックを尊重しつつ、スコアで優先度と次アクションを再設計。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.project import Project
from app.services import sales_assessment_service as sas
from app.services import sales_copilot_service as v1

logger = logging.getLogger("sales_copilot_v2")


def _clamp(v: int) -> int:
    return max(0, min(100, int(v)))


def _priority_label(score: int) -> str:
    return "high" if score >= 67 else ("medium" if score >= 40 else "low")


def combine_v2(base_card: dict, assessment: dict, contact: dict) -> dict:
    """v1 カード＋3 スコア＋連絡先から v2 の推奨を作る（DB 非依存・純粋関数）。

    Returns: {decision, base_decision, priority_score, priority_label,
              next_action, reason, tags}
    """
    jm = assessment["japan_market_fit"]["score"]
    ex = assessment["exclusivity"]["score"]
    mk = assessment["makuake_fit"]["score"]
    overall = assessment["overall_priority_score"]
    confidence = assessment.get("confidence", 0)
    base_decision = base_card.get("decision")
    has_contact = bool(contact.get("has_email") or contact.get("has_form"))

    # 強み/注意タグ（営業メールの根拠や画面バッジに使う）
    tags: list[str] = []
    if ex >= 67:
        tags.append("独占販売の好機")
    if jm >= 67:
        tags.append("日本市場適性が高い")
    if mk >= 67:
        tags.append("Makuake再ローンチ有望")
    if confidence < 40:
        tags.append("データ不足（要追加調査）")

    # v1 の営業ワークフロー状態はスコアで上書きしない（既に接触/商談中の案件へ
    # 「今すぐ営業メール」を出さない）。見送り/成約に加え、待機/フォロー/商談も尊重。
    _preserve = ("closed", "drop", "waiting", "needs_followup", "needs_negotiation")

    # v2 優先度スコア：適性の総合点を土台に、到達性と見送りで調整。
    priority = overall
    if has_contact:
        priority += 8
    if base_decision in ("drop", "closed"):
        priority -= 25
    priority = _clamp(priority)

    # v2 判断・次アクション（v1 の営業ワークフロー状態を尊重）
    if base_decision in _preserve:
        decision = base_decision
        next_action = base_card.get("next_action")
        reason = (base_card.get("reasons") or [None])[0] or "v1 の営業状態を尊重"
    elif overall < 35 and confidence >= 50:
        decision = "deprioritize"
        next_action = "優先度を下げる：他の適性が高い案件を優先"
        reason = f"日本市場/独占/Makuake 適性が総じて低い（総合 {overall}）"
    elif has_contact and ex >= 60 and jm >= 55:
        decision = "sell_now_exclusive"
        extra = "・Makuake再ローンチも提案可" if mk >= 60 else ""
        next_action = f"優先営業：独占販売を提案する営業メールを送る{extra}"
        reason = f"連絡先あり・独占可能性 {ex}・日本市場適性 {jm} で営業好機"
    elif not has_contact and overall >= 55:
        decision = "needs_contact"
        next_action = "有望案件：Contact Intelligence で連絡先を確保する"
        reason = f"適性は高い（総合 {overall}）が連絡先が未取得"
    else:
        # スコアが決め手にならない場合は v1 の判断・次アクションを踏襲
        decision = base_decision
        next_action = base_card.get("next_action")
        reason = (base_card.get("reasons") or [None])[0] or "v1 判断を踏襲"

    return {
        "decision": decision,
        "base_decision": base_decision,
        "priority_score": priority,
        "priority_label": _priority_label(priority),
        "next_action": next_action,
        "reason": reason,
        "tags": tags,
    }


# ---------------- DB 連携 ----------------
def _assessment_dict(db: Session, project: Project) -> tuple[dict, bool, int]:
    """保存済みアセスメント（あれば）→ dict。無ければその場で算出（保存しない）。

    Returns: (assessment_dict, saved, confidence)
    """
    row = sas.get_latest(db, project.id)
    if row is not None and row.details_json:
        d = row.details_json
        return (
            {
                "japan_market_fit": d.get("japan_market_fit", {}),
                "exclusivity": d.get("exclusivity", {}),
                "makuake_fit": d.get("makuake_fit", {}),
                "overall_priority_score": row.overall_priority_score or 0,
                "confidence": row.confidence or 0,
                "engine": row.engine,
            },
            True,
            row.confidence or 0,
        )
    # 未算出：その場で計算（読み取り専用・非保存）
    sig = sas._gather_signals(db, project)
    result = sas.assess(sig)
    return result, False, result["confidence"]


def _contact_flags(base_card: dict) -> dict:
    """v1 カードの summary から到達性フラグを取り出す。"""
    summary = base_card.get("summary") or {}
    cs = str(summary.get("contact_status") or "")
    return {
        "has_email": bool(base_card.get("recommended_email"))
        or ("メール" in cs and "未" not in cs),
        "has_form": "フォーム" in cs,
    }


def _japan_block(db: Session, project: Project) -> dict:
    """日本販売状況の状態（ジョブ進行中なら running）を根拠つきで返す。"""
    from sqlalchemy import desc, select

    from app.models.contact_intelligence_job import (
        CIJobType,
        ContactIntelligenceJob,
    )

    job = db.scalar(
        select(ContactIntelligenceJob)
        .where(
            ContactIntelligenceJob.project_id == project.id,
            ContactIntelligenceJob.job_type == CIJobType.japan_sales_check.value,
        )
        .order_by(desc(ContactIntelligenceJob.id))
        .limit(1)
    )
    job_status = job.status if job is not None else None
    active = job_status if job_status in ("queued", "running") else None
    jsc = sas._latest_japan_sales_check(db, project.id)
    interp = sas.interpret_japan_check(jsc, job_status=active)
    interp["job_id"] = job.id if job is not None else None
    interp["job_status"] = job_status
    return interp


def _assessment_state(saved_row, japan: dict) -> str:
    """画面表示用の評価状態を 1 つ返す。

    checking_japan / recompute_pending / provisional / failed / data_insufficient
    / evaluated のいずれか。
    """
    if japan["status"] in ("queued", "running"):
        return "checking_japan"
    if saved_row is None:
        return "data_insufficient"
    details = saved_row.details_json or {}
    prov = bool(details.get("provisional"))
    conf = saved_row.confidence or 0
    created = saved_row.created_at.isoformat() if saved_row.created_at else None
    # 日本販売チェックがアセスメントより新しい → 再評価待ち
    if japan.get("checked_at") and created and japan["checked_at"] > created:
        return "recompute_pending"
    if prov:
        return "provisional"
    if japan["status"] == "failed" and conf < 50:
        return "failed"
    if conf < 40:
        return "data_insufficient"
    return "evaluated"


def build_v2_card(db: Session, project: Project) -> dict:
    """1 案件の Sales Copilot v2 統合カードを返す。"""
    base_card = v1.build_card(db, project)
    saved_row = sas.get_latest(db, project.id)
    contact = _contact_flags(base_card)
    japan = _japan_block(db, project)

    # 未評価：その場で評価しない。0 点 / grade E ではなく None（未評価）を返す。
    if saved_row is None:
        return {
            "project_id": project.id,
            "title": project.title,
            "source_site": project.source_site,
            "maker_name": project.maker_name,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
            "funding": (base_card.get("summary") or {}).get("funding"),
            "decision": "not_evaluated",
            "base_decision": "not_evaluated",
            "priority_score": None,
            "priority_grade": None,
            "priority_label": "unrated",
            "next_action": "営業適性を評価してください（バッチ評価 or POST /projects/{id}/sales-assessment）",
            "reason": "営業適性アセスメント未実施（0 点ではなく未評価）",
            "tags": ["未評価"],
            "v1_decision": "not_evaluated",
            "v2_decision": "not_evaluated",
            "decision_changed": False,
            "decision_change_reason": None,
            "v1_decision_label": base_card.get("decision_label"),
            "visibility_reason": "not_evaluated",
            "assessment": {
                "japan_market_fit": {"score": None, "grade": None, "level": None, "reasons": []},
                "exclusivity": {"score": None, "grade": None, "level": None, "reasons": []},
                "makuake_fit": {"score": None, "grade": None, "level": None, "reasons": []},
                "overall_priority_score": None, "overall_grade": None,
                "confidence": None, "engine": None, "saved": False,
                "evaluated_at": None, "missing_data": None, "state": "not_evaluated",
            },
            "japan_sales_check": {
                "status": japan["status"], "result": japan["result"],
                "confidence": japan["confidence"], "evidence": japan["evidence"],
                "source_urls": japan["source_urls"], "checked_at": japan["checked_at"],
                "error_reason": japan["error_reason"], "version": japan["version"],
                "job_id": japan.get("job_id"), "job_status": japan.get("job_status"),
            },
            "contact": {
                "has_email": contact.get("has_email"),
                "has_form": contact.get("has_form"),
                "recommended_channel": base_card.get("recommended_channel"),
                "recommended_email": base_card.get("recommended_email"),
            },
            "pipeline": {
                "sales_status": (base_card.get("summary") or {}).get("sales_status"),
                "last_action": (base_card.get("summary") or {}).get("last_action"),
                "contact_person_found": (base_card.get("summary") or {}).get("contact_person_found"),
            },
            "actions": base_card.get("actions"),
            "assessment_state": "not_evaluated",
        }

    assessment, saved, _conf = _assessment_dict(db, project)
    rec = combine_v2(base_card, assessment, contact)
    state = _assessment_state(saved_row, japan)

    def _score_block(key: str) -> dict:
        blk = assessment.get(key, {}) or {}
        return {
            "score": blk.get("score"),
            "grade": sas.grade(blk.get("score")),
            "level": blk.get("level"),
            "reasons": (blk.get("reasons") or [])[:3],
        }

    overall = assessment.get("overall_priority_score")
    missing = (saved_row.details_json or {}).get("missing_data") if saved_row else None
    base_decision = rec["base_decision"]
    v2_decision = rec["decision"]

    return {
        "project_id": project.id,
        "title": project.title,
        "source_site": project.source_site,
        "maker_name": project.maker_name,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        "funding": (base_card.get("summary") or {}).get("funding"),
        # v2 判断
        "decision": v2_decision,
        "base_decision": base_decision,
        "priority_score": rec["priority_score"],
        "priority_grade": sas.grade(rec["priority_score"]),
        "priority_label": rec["priority_label"],
        "next_action": rec["next_action"],
        "reason": rec["reason"],
        "tags": rec["tags"],
        # v1/v2 の整合性（v1 は上書きしない・別フィールドで比較）
        "v1_decision": base_decision,
        "v2_decision": v2_decision,
        "decision_changed": base_decision != v2_decision,
        "decision_change_reason": rec["reason"] if base_decision != v2_decision else None,
        "v1_decision_label": base_card.get("decision_label"),
        "visibility_reason": _visibility_reason(v2_decision, state, japan, contact),
        # 3 スコア（0〜100 + grade + 理由）
        "assessment": {
            "japan_market_fit": _score_block("japan_market_fit"),
            "exclusivity": _score_block("exclusivity"),
            "makuake_fit": _score_block("makuake_fit"),
            "overall_priority_score": overall,
            "overall_grade": sas.grade(overall),
            "confidence": assessment.get("confidence"),
            "engine": assessment.get("engine"),
            "saved": saved,
            "evaluated_at": saved_row.created_at.isoformat()
            if saved_row and saved_row.created_at else None,
            "missing_data": missing,
            "state": state,
        },
        # 日本販売状況（根拠つき・検索ゼロ＝未販売と断定しない）
        "japan_sales_check": {
            "status": japan["status"],
            "result": japan["result"],
            "confidence": japan["confidence"],
            "evidence": japan["evidence"],
            "source_urls": japan["source_urls"],
            "checked_at": japan["checked_at"],
            "error_reason": japan["error_reason"],
            "version": japan["version"],
            "job_id": japan.get("job_id"),
            "job_status": japan.get("job_status"),
        },
        # 連絡先状態（既存パイプライン流用）
        "contact": {
            "has_email": contact.get("has_email"),
            "has_form": contact.get("has_form"),
            "recommended_channel": base_card.get("recommended_channel"),
            "recommended_email": base_card.get("recommended_email"),
        },
        # 既存パイプラインの状態（流用）
        "pipeline": {
            "sales_status": (base_card.get("summary") or {}).get("sales_status"),
            "last_action": (base_card.get("summary") or {}).get("last_action"),
            "contact_person_found": (base_card.get("summary") or {}).get("contact_person_found"),
        },
        # v1 の次アクション候補（画面の詳細操作用）
        "actions": base_card.get("actions"),
        "assessment_state": state,
    }


# ---------------- ダッシュボード（読み取り専用・バッチ取得） ----------------
# 一覧では保存済みデータだけを読む。未評価案件はその場で評価せず not_evaluated で返す
# （外部HTTP/Playwright/Claude/Assessment保存/Japanチェック起動は一切しない）。

# 営業状況 → v1 相当の「営業ワークフロー状態」。engaged はスコアで上書きしない。
_SALES_STATUS_BASE = {
    "won": "closed",
    "rejected": "closed",
    "negotiating": "needs_negotiation",
    "replied": "needs_negotiation",
    "contacted": "waiting",
    "awaiting_reply": "waiting",
}


def _batch_latest_map(db: Session, model, project_ids: list[int]):
    """各 project_id の最新（最大 id）行を 1 クエリで取得して map で返す。"""
    from sqlalchemy import func, select

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


def _batch_latest_japan_jobs(db: Session, project_ids: list[int]):
    from sqlalchemy import func, select

    from app.models.contact_intelligence_job import (
        CIJobType,
        ContactIntelligenceJob,
    )

    if not project_ids:
        return {}
    sub = (
        select(
            ContactIntelligenceJob.project_id,
            func.max(ContactIntelligenceJob.id).label("mid"),
        )
        .where(
            ContactIntelligenceJob.project_id.in_(project_ids),
            ContactIntelligenceJob.job_type == CIJobType.japan_sales_check.value,
        )
        .group_by(ContactIntelligenceJob.project_id)
        .subquery()
    )
    rows = db.scalars(
        select(ContactIntelligenceJob).join(
            sub, ContactIntelligenceJob.id == sub.c.mid
        )
    )
    return {r.project_id: r for r in rows}


def _visibility_reason(decision: str, state: str, japan: dict, contact: dict) -> str:
    """「今日やること/ランキングにどう扱われるか」の理由（画面で確認できる）。"""
    has_contact = bool(contact.get("has_email") or contact.get("has_form"))
    if decision in ("closed", "drop"):
        return "not_sales_target"
    if state == "not_evaluated":
        return "not_evaluated"
    if state == "data_insufficient":
        return "data_insufficient"
    if state == "checking_japan":
        return "japan_not_checked"
    if decision in ("sell_now_exclusive", "sell_now", "needs_email"):
        return "actionable_today"
    if decision == "needs_contact" or not has_contact:
        return "needs_contact"
    if decision == "deprioritize":
        return "low_priority"
    return "actionable_today"


def _dashboard_card(project: Project, sa_row, japan_job, jsc, contact: dict) -> dict:
    """保存済みデータだけから v2 カードを作る（DB アクセスなし・純粋）。"""
    japan_active = (
        japan_job.status
        if japan_job is not None and japan_job.status in ("queued", "running")
        else None
    )
    japan = sas.interpret_japan_check(jsc, job_status=japan_active)
    japan["job_id"] = japan_job.id if japan_job is not None else None
    japan["job_status"] = japan_job.status if japan_job is not None else None

    japan_block = {
        "status": japan["status"], "result": japan["result"],
        "confidence": japan["confidence"], "evidence": japan["evidence"],
        "source_urls": japan["source_urls"], "checked_at": japan["checked_at"],
        "error_reason": japan["error_reason"], "version": japan["version"],
        "job_id": japan["job_id"], "job_status": japan["job_status"],
    }

    base = {
        "project_id": project.id,
        "title": project.title,
        "source_site": project.source_site,
        "maker_name": project.maker_name,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        "japan_sales_check": japan_block,
        "contact": {
            "has_email": contact.get("has_email", False),
            "has_form": contact.get("has_form", False),
            "recommended_channel": contact.get("recommended_channel"),
            "recommended_email": contact.get("recommended_email"),
        },
    }

    if sa_row is None:
        # 未評価：その場で評価しない。スコアは 0 でなく None（未評価は 0 点・grade E に
        # しない）。ランキングは None を末尾に回す。
        base.update({
            "decision": "not_evaluated",
            "base_decision": "not_evaluated",
            "priority_score": None,
            "priority_grade": None,
            "priority_label": "unrated",
            "next_action": "営業適性を評価してください（バッチ評価 or POST /projects/{id}/sales-assessment）",
            "reason": "営業適性アセスメント未実施（0 点ではなく未評価）",
            "tags": ["未評価"],
            "v1_decision": "not_evaluated",
            "v2_decision": "not_evaluated",
            "decision_changed": False,
            "decision_change_reason": None,
            "visibility_reason": "not_evaluated",
            "assessment": {
                "japan_market_fit": {"score": None, "grade": None, "level": None, "reasons": []},
                "exclusivity": {"score": None, "grade": None, "level": None, "reasons": []},
                "makuake_fit": {"score": None, "grade": None, "level": None, "reasons": []},
                "overall_priority_score": None, "overall_grade": None,
                "confidence": None, "engine": None, "saved": False,
                "evaluated_at": None, "missing_data": None, "state": "not_evaluated",
            },
            "assessment_state": "not_evaluated",
        })
        return base

    d = sa_row.details_json or {}
    assessment = {
        "japan_market_fit": d.get("japan_market_fit", {}),
        "exclusivity": d.get("exclusivity", {}),
        "makuake_fit": d.get("makuake_fit", {}),
        "overall_priority_score": sa_row.overall_priority_score or 0,
        "confidence": sa_row.confidence or 0,
        "engine": sa_row.engine,
    }
    # v1 相当の base_decision は営業状況から軽量に導く（engaged はスコアで上書きしない）。
    base_decision = _SALES_STATUS_BASE.get(project.sales_status) or (
        "needs_email" if (contact.get("has_email") or contact.get("has_form"))
        else "needs_contact"
    )
    base_card = {"decision": base_decision, "next_action": None, "reasons": [None]}
    rec = combine_v2(base_card, assessment, contact)
    state = _assessment_state(sa_row, japan)

    def _sb(key: str) -> dict:
        blk = d.get(key, {}) or {}
        return {"score": blk.get("score"), "grade": sas.grade(blk.get("score")),
                "level": blk.get("level"), "reasons": (blk.get("reasons") or [])[:3]}

    overall = assessment["overall_priority_score"]
    base.update({
        "decision": rec["decision"],
        "base_decision": rec["base_decision"],
        "priority_score": rec["priority_score"],
        "priority_grade": sas.grade(rec["priority_score"]),
        "priority_label": rec["priority_label"],
        "next_action": rec["next_action"],
        "reason": rec["reason"],
        "tags": rec["tags"],
        "v1_decision": rec["base_decision"],
        "v2_decision": rec["decision"],
        "decision_changed": rec["base_decision"] != rec["decision"],
        "decision_change_reason": rec["reason"] if rec["base_decision"] != rec["decision"] else None,
        "visibility_reason": _visibility_reason(
            rec["decision"], state, japan, contact
        ),
        "assessment": {
            "japan_market_fit": _sb("japan_market_fit"),
            "exclusivity": _sb("exclusivity"),
            "makuake_fit": _sb("makuake_fit"),
            "overall_priority_score": overall,
            "overall_grade": sas.grade(overall),
            "confidence": sa_row.confidence,
            "engine": sa_row.engine,
            "saved": True,
            "evaluated_at": sa_row.created_at.isoformat() if sa_row.created_at else None,
            "missing_data": d.get("missing_data"),
            "state": state,
        },
        "assessment_state": state,
    })
    return base


def copilot_v2_dashboard(
    db: Session, *, per_bucket: int = 5, scan_limit: int = 200
) -> dict:
    """v2 ダッシュボード（読み取り専用・バッチ取得・N+1 なし）。

    保存済みの Assessment / Japan チェック状態 / 連絡先だけを読む。未評価案件は
    その場で評価せず not_evaluated で返す。外部HTTP・Playwright・Claude・
    Assessment 保存・Japan チェック起動は一切行わない。
    """
    from sqlalchemy import select

    from app.models.contact_discovery import ContactDiscovery
    from app.models.japan_sales_check import JapanSalesCheck
    from app.models.project import SALES_TARGET_SITES, not_archived_clause
    from app.models.sales_assessment import SalesAssessment

    values = [s.value for s in SALES_TARGET_SITES]
    stmt = (
        select(Project)
        .where(Project.source_site.in_(values), not_archived_clause())
        .order_by(Project.latest_score.desc().nullslast(), Project.updated_at.desc())
        .limit(scan_limit)
    )
    projects = list(db.scalars(stmt))
    ids = [p.id for p in projects]

    # バッチ取得（各 1 クエリ）：最新 Assessment / Japan ジョブ / Japan チェック / 連絡先
    sa_map = _batch_latest_map(db, SalesAssessment, ids)
    jjob_map = _batch_latest_japan_jobs(db, ids)
    jsc_map = _batch_latest_map(db, JapanSalesCheck, ids)
    cd_map = _batch_latest_map(db, ContactDiscovery, ids)

    from app.services import contact_discovery_service as cds

    contact_flags: dict[int, dict] = {}
    for pid, cd in cd_map.items():
        contact_flags[pid] = {
            "has_email": bool(cd.primary_email or cd.discovered_emails),
            "has_form": bool(cd.primary_contact_form_url or cd.discovered_forms),
            "recommended_channel": cd.recommended_channel,
            "recommended_email": cd.primary_email,
            "official": bool(cds.official_site_or_none(cd.official_site_url)),
        }

    cards = [
        _dashboard_card(
            p, sa_map.get(p.id), jjob_map.get(p.id), jsc_map.get(p.id),
            contact_flags.get(p.id, {}),
        )
        for p in projects
    ]
    # 未評価（priority_score=None）は末尾へ回す（0 点扱いにしない）。
    cards.sort(
        key=lambda c: c["priority_score"] if c["priority_score"] is not None else -1,
        reverse=True,
    )

    counts: dict[str, int] = {}
    for c in cards:
        counts[c["decision"]] = counts.get(c["decision"], 0) + 1

    def _bucket(*decisions: str) -> list[dict]:
        items = [c for c in cards if c["decision"] in decisions]
        return items[:per_bucket]

    top = [c for c in cards if c["decision"] not in ("closed", "drop", "deprioritize", "waiting")]
    return {
        "top_action": top[0] if top else None,
        "priority_ranking": cards[:per_bucket],
        "sell_now": _bucket("sell_now_exclusive", "sell_now"),
        "needs_contact": _bucket("needs_contact"),
        "needs_email": _bucket("needs_email"),
        "deprioritize": _bucket("deprioritize", "drop"),
        "data_insufficient": _bucket("data_insufficient"),
        "not_evaluated": _bucket("not_evaluated"),
        "counts": counts,
        "scanned": len(cards),
        # フロントの一覧（フィルター/並び替えはクライアント側で行う）用の全カード。
        "items": cards,
        # 一覧に出す集計値（japan 未実施件数・データ不足件数など）
        "summary_counts": {
            "japan_not_checked": sum(
                1 for c in cards
                if c["japan_sales_check"]["status"] in ("not_checked", "failed")
            ),
            "not_evaluated": sum(
                1 for c in cards if c["assessment_state"] == "not_evaluated"
            ),
            "checking_japan": sum(
                1 for c in cards if c["assessment_state"] == "checking_japan"
            ),
            "data_insufficient": sum(
                1 for c in cards if c["assessment_state"] == "data_insufficient"
            ),
            # 保存済みで confidence が低い案件のみ（未評価は含めない）
            "low_confidence": sum(
                1 for c in cards
                if c["assessment"]["saved"] and (c["assessment"]["confidence"] or 0) < 40
            ),
        },
    }
