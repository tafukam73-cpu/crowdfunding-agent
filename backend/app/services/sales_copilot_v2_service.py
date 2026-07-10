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
    assessment, saved, _conf = _assessment_dict(db, project)
    saved_row = sas.get_latest(db, project.id)
    contact = _contact_flags(base_card)
    rec = combine_v2(base_card, assessment, contact)
    japan = _japan_block(db, project)
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


def copilot_v2_dashboard(
    db: Session, *, per_bucket: int = 5, scan_limit: int = 200
) -> dict:
    """v2 ダッシュボード：v2 優先度スコアでランキングし、バケットに振り分ける。"""
    from sqlalchemy import select

    from app.models.project import SALES_TARGET_SITES

    values = [s.value for s in SALES_TARGET_SITES]
    stmt = (
        select(Project)
        .where(Project.source_site.in_(values))
        .order_by(Project.latest_score.desc().nullslast(), Project.updated_at.desc())
        .limit(scan_limit)
    )
    projects = list(db.scalars(stmt))
    cards = [build_v2_card(db, p) for p in projects]
    cards.sort(key=lambda c: c["priority_score"], reverse=True)

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
            "checking_japan": sum(
                1 for c in cards if c["assessment_state"] == "checking_japan"
            ),
            "data_insufficient": sum(
                1 for c in cards if c["assessment_state"] == "data_insufficient"
            ),
            "low_confidence": sum(
                1 for c in cards
                if (c["assessment"]["confidence"] or 0) < 40
            ),
        },
    }
