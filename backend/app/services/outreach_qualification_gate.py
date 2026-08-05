"""営業アウトリーチの送信前関門（PR-5）。

Gmail 下書き作成の**直前**に pre_outreach 判定を必ず実行し、``clear`` 以外は
``LeadQualificationBlocked`` を送出して止める。

## このシステムの「送信」について

このアプリは**メールを送信しない**（`app/email/providers/base.py` の設計どおり
「下書きを作成する。送信は行わない」）。Gmail プロバイダのスコープも
``gmail.compose`` のみ。したがって関門を置くのは次の 2 箇所になる。

  1. **Gmail 下書き作成**（``email_delivery_service.create_provider_draft``）
     … 外部（ユーザーの Gmail）へ実際に書き込む唯一の地点。**主関門**
  2. **Gmail compose URL の発行** … 宛先・件名・本文入りの作成画面 URL。
     開けば「送信」を押すだけの状態になるため送信導線。**副関門**

**限界**: 人が手で Gmail を開いて送る経路は、アプリからは止められない。
この関門は「アプリが送信を用意すること」を止めるものであって、
物理的な送信禁止ではない。

## 判定の方針

- **fail closed**: 判定を完了できない場合は送信を止める（409）。
  調査ゲート（pre_research）が fail open なのと非対称だが意図的で、
  誤送信は取り返しがつかないため（CLAUDE.md §1）。
- ``review`` も止める。pre_outreach の review は maker 未確認・ブランド所有者
  不明・代理店疑いなど、そのまま送ると誤送信になる事項が中心のため。
- 通すのは ``clear``、または**人が明示的に override した clear** のみ。
  AI / Copilot による自動 override は行わない。

## 履歴を増やしすぎない

送信操作のたびに ``run()`` すると、ボタン連打や再試行で履歴が膨らむ。
判定入力のダイジェスト（``signals_digest``）が一致し、かつ 24 時間以内なら
**保存済み履歴を再利用**して履歴を増やさない。入力が変われば 1 行追加する。

**画面表示だけでは判定しない**（GET 系は PR-4 の時点で履歴を書かない）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.project import Project
from app.services import lead_qualification_service as lqs

logger = logging.getLogger("outreach_qualification_gate")

# --- 鮮度（層1: 判定そのもの。層2 の証跡鮮度は PR-1 の Finding.stale が担う） --- #
#: 判定履歴を再利用してよい上限。送信は「今この瞬間」の判断なので短くする。
DECISION_MAX_AGE_HOURS = 24
#: 人の override を有効とみなす上限。機械判定より重いが永続はさせない。
OVERRIDE_MAX_AGE_HOURS = 72

#: 予約メタに入れる入力ダイジェストのキー（新規 DB 列は作らない）。
DIGEST_KEY = "signals_digest"

#: 409 に載せてよいメッセージ。
MESSAGE_BLOCKED = "営業対象判定によりGmail下書きを作成できません"
MESSAGE_UNAVAILABLE = "営業対象判定を完了できないため、Gmail下書きを作成できません"

#: 監査記録で判定が取れなかったことを表す印。
AUDIT_UNAVAILABLE = "qualification_unavailable"


class LeadQualificationBlocked(Exception):
    """pre_outreach 判定により送信準備を進められない。

    ``payload`` は 409 レスポンスへそのまま載せてよい**安全な dict**。
    数値スコア・確率・証跡本文・メールアドレス・内部ロケータを含めない。
    """

    def __init__(self, payload: dict, *, message: str = MESSAGE_BLOCKED):
        self.payload = payload
        self.message = message
        super().__init__(message)

    def as_detail(self) -> dict:
        return {"message": self.message, "qualification": self.payload}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
#  signals_digest
# --------------------------------------------------------------------------- #
def _normalize(value: Any) -> Any:
    """ダイジェスト計算のために値を正規化する。

    - dict はキー順に依存しないよう整列する
    - list は順序を保つ（順序に意味がある証跡リストのため）が、要素は正規化する
    - datetime / date は ISO8601（UTC）へ
    - None と空文字は同一視する（片方だけ変わってダイジェストがぶれないように）
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _aware(value).astimezone(timezone.utc).isoformat()
    if hasattr(value, "isoformat"):  # date
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, (int, float, bool, str)):
        return value
    return str(value)


def signals_digest(signals: dict) -> str:
    """判定入力の安定ダイジェスト（SHA-256）。

    **秘密情報を含めない。** メール本文・トークン等は signals に入らない設計だが、
    念のためメールアドレスはローカル部を落としてドメインだけを使う。
    """
    safe = dict(signals or {})
    emails = safe.get("business_emails") or []
    safe["business_emails"] = [
        {
            "domain": (str(e.get("email") or "").rsplit("@", 1)[-1] or None),
            "source_url": e.get("source_url"),
            "checked_at": e.get("checked_at"),
            "role": e.get("role"),
        }
        for e in emails
    ]
    payload = json.dumps(
        _normalize(safe), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
#  履歴の再利用 / override の検証
# --------------------------------------------------------------------------- #
def _digest_of_row(row) -> str | None:
    meta = lqs.qualification_meta(row) or {}
    value = meta.get(DIGEST_KEY)
    return value if isinstance(value, str) else None


def is_reusable(row, digest: str, *, now: datetime) -> bool:
    """保存済み判定をそのまま使ってよいか（履歴を増やさないための条件）。"""
    if row is None or row.stage != lqs.STAGE_PRE_OUTREACH:
        return False
    if _digest_of_row(row) != digest:
        return False
    created = _aware(getattr(row, "created_at", None))
    if created is None:
        return False
    return created >= now - timedelta(hours=DECISION_MAX_AGE_HOURS)


def _has_stale_blocker(row) -> bool:
    """証跡の鮮度切れで降格された Finding が残っていないか（PR-1 の判定を尊重）。"""
    for finding in lqs.findings_of(row):
        if finding.get("verdict") == lqs.VERDICT_STALE:
            return True
    return False


def valid_override(row, digest: str, *, now: datetime) -> bool:
    """人の override をそのまま採用してよいか。**1 つでも欠ければ無効。**

    条件: 保存済み / stage=pre_outreach / overridden / effective=clear /
    reason 非空 / evidence_url が http(s) / 72 時間以内 / 入力ダイジェスト一致 /
    stale 相当の Finding が無いこと。

    「最新履歴であること」「後続 recheck が無いこと」は、呼び出し側が
    ``get_latest(stage=pre_outreach)`` の 1 件だけを渡すことで担保する。
    """
    if row is None or row.stage != lqs.STAGE_PRE_OUTREACH:
        return False
    meta = lqs.qualification_meta(row) or {}
    if not meta.get("overridden"):
        return False
    if meta.get("effective_decision") != lqs.DECISION_CLEAR:
        return False
    if row.decision != lqs.DECISION_CLEAR:
        return False
    if not (row.override_reason or "").strip():
        return False
    url = (row.override_evidence_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False
    created = _aware(getattr(row, "created_at", None))
    if created is None or created < now - timedelta(hours=OVERRIDE_MAX_AGE_HOURS):
        return False
    if _digest_of_row(row) != digest:
        return False
    if _has_stale_blocker(row):
        return False
    return True


# --------------------------------------------------------------------------- #
#  安全なレスポンス payload
# --------------------------------------------------------------------------- #
def _reasons_from(row=None, result=None, *, limit: int = 5) -> list[str]:
    """409 に載せる理由文。**証跡オブジェクトごと返さない**（URL を出さない）。"""
    severities = (lqs.SEVERITY_BLOCKER, lqs.SEVERITY_REVIEW)
    if row is not None:
        items = [
            f.get("reason") for f in lqs.findings_of(row)
            if f.get("severity") in severities and f.get("reason")
        ]
    elif result is not None:
        items = [
            f.reason for f in result.findings
            if f.severity in severities and f.reason
        ]
    else:
        items = []
    seen: list[str] = []
    for text in items:
        if text not in seen:
            seen.append(text)
    return seen[:limit]


def safe_payload(*, row=None, result=None, persisted: bool) -> dict:
    """409 用の安全な qualification 情報。

    載せない: 数値 confidence / score / probability / forecast / 返信率 /
    成功率 / makuake_fit / japan_crowdfunding_score / Evidence 本文 /
    メールアドレス / internal_db の URL。
    """
    if row is not None:
        meta = lqs.qualification_meta(row) or {}
        machine = meta.get("machine_decision") or row.decision
        effective = meta.get("effective_decision") or row.decision
        checked_at = _aware(getattr(row, "created_at", None))
        return {
            "stage": row.stage,
            "decision": effective,
            "machine_decision": machine,
            "effective_decision": effective,
            "overridden": bool(meta.get("overridden")),
            "blocker_codes": list(row.blocker_codes or []),
            "review_codes": list(row.review_codes or []),
            "reasons": _reasons_from(row=row),
            "checked_at": checked_at.isoformat() if checked_at else None,
            "persisted": persisted,
        }
    if result is not None:
        return {
            "stage": result.stage,
            "decision": result.decision,
            "machine_decision": result.decision,
            "effective_decision": result.decision,
            "overridden": False,
            "blocker_codes": list(result.blocker_codes),
            "review_codes": list(result.review_codes),
            "reasons": _reasons_from(result=result),
            "checked_at": result.evaluated_at.isoformat(),
            "persisted": persisted,
        }
    # 判定そのものを作れなかった（fail closed）。
    return {
        "stage": lqs.STAGE_PRE_OUTREACH,
        "decision": None,
        "machine_decision": None,
        "effective_decision": None,
        "overridden": False,
        "blocker_codes": [],
        "review_codes": [],
        "reasons": ["営業対象判定を完了できませんでした"],
        "checked_at": None,
        "persisted": False,
    }


# --------------------------------------------------------------------------- #
#  関門本体
# --------------------------------------------------------------------------- #
def evaluate(db: Session, project: Project) -> tuple[str, dict, Any]:
    """pre_outreach 判定を取得する（必要なら履歴を 1 行追加）。

    Returns: ``(decision, payload, row_or_none)``
    Raises: 例外は送出しない。判定できない場合は decision=None で返す。
    """
    now = _now()
    try:
        signals = lqs.gather_signals(db, project)
        digest = signals_digest(signals)
    except Exception as exc:  # noqa: BLE001  fail closed
        logger.warning("qualification signals failed (project=%s): %s", project.id, exc)
        return None, safe_payload(persisted=False), None

    try:
        latest = lqs.get_latest(db, project.id, stage=lqs.STAGE_PRE_OUTREACH)
    except Exception as exc:  # noqa: BLE001  fail closed
        logger.warning("qualification history failed (project=%s): %s", project.id, exc)
        return None, safe_payload(persisted=False), None

    # 1. 有効な override は最優先で採用する（人の明示判断）。
    if valid_override(latest, digest, now=now):
        return lqs.DECISION_CLEAR, safe_payload(row=latest, persisted=True), latest

    # 2. 入力が変わっておらず新しい判定なら再利用（履歴を増やさない）。
    if is_reusable(latest, digest, now=now):
        meta = lqs.qualification_meta(latest) or {}
        decision = meta.get("effective_decision") or latest.decision
        return decision, safe_payload(row=latest, persisted=True), latest

    # 3. 再判定して履歴を 1 行追加する。
    try:
        result = lqs.qualify(signals, lqs.STAGE_PRE_OUTREACH)
        row = lqs._append_history(db, project, result)
        _attach_digest(row, digest)
        db.commit()
        db.refresh(row)
    except Exception as exc:  # noqa: BLE001  fail closed
        logger.warning("qualification run failed (project=%s): %s", project.id, exc)
        db.rollback()
        return None, safe_payload(persisted=False), None
    return result.decision, safe_payload(row=row, persisted=True), row


def _attach_digest(row, digest: str) -> None:
    """予約メタへ入力ダイジェストを埋める（新規 DB 列を作らない）。

    通常 Finding として数えず、``evidence_count`` にも加算しない
    （``findings_of`` / ``qualification_meta`` が予約キーで判別する）。
    """
    # SQLAlchemy の JSON 列は「同じオブジェクトを書き戻す」と変更を検知しない。
    # 必ず新しいリスト／新しい dict を組み立てて代入する。
    updated: list = []
    found = False
    for item in (row.findings_json or []):
        if isinstance(item, dict) and lqs.META_KEY in item:
            meta = dict(item[lqs.META_KEY] or {})
            meta[DIGEST_KEY] = digest
            updated.append({lqs.META_KEY: meta})
            found = True
        else:
            updated.append(item)
    if not found:
        updated.append({lqs.META_KEY: {DIGEST_KEY: digest}})
    row.findings_json = updated


def require_clear(db: Session, project: Project) -> dict:
    """送信準備（Gmail 下書き作成）を進めてよいか。

    **``clear`` 以外はすべて ``LeadQualificationBlocked``**（review も止める）。
    判定できなかった場合も止める（fail closed）。

    Returns: 通過した場合の安全な qualification payload。
    """
    decision, payload, _row = evaluate(db, project)
    if decision == lqs.DECISION_CLEAR:
        return payload
    if decision is None:
        raise LeadQualificationBlocked(payload, message=MESSAGE_UNAVAILABLE)
    logger.info(
        "outreach blocked by qualification: project=%s decision=%s blockers=%s",
        project.id, decision, payload.get("blocker_codes"),
    )
    raise LeadQualificationBlocked(payload)


def audit_note(db: Session, project: Project) -> str:
    """``mark_sent`` 用の監査記録（送信を止めない）。

    メールアドレス・証跡本文・内部ロケータは書かない。判定できない場合も
    ``mark_sent`` 自体は成功させ、その事実だけ残す。
    """
    try:
        decision, payload, _row = evaluate(db, project)
    except Exception as exc:  # noqa: BLE001  監査失敗で記録を止めない
        logger.warning("qualification audit failed (project=%s): %s", project.id, exc)
        return f"LQE監査: {AUDIT_UNAVAILABLE}"
    if decision is None:
        return f"LQE監査: {AUDIT_UNAVAILABLE}"
    parts = [
        f"decision={decision}",
        f"stage={payload.get('stage')}",
        f"checked_at={payload.get('checked_at')}",
        f"overridden={str(payload.get('overridden')).lower()}",
    ]
    codes = payload.get("blocker_codes") or []
    if codes:
        parts.append("blocker_codes=" + ",".join(codes))
    return "LQE監査: " + " ".join(parts)


def latest_decision(db: Session, project_id: int) -> str | None:
    """**判定を実行せず**、保存済みの最新 pre_outreach 判定だけを返す。

    画面表示（compose URL の可否など）から使う。履歴を増やさない。
    """
    try:
        row = lqs.get_latest(db, project_id, stage=lqs.STAGE_PRE_OUTREACH)
    except Exception as exc:  # noqa: BLE001
        logger.warning("latest qualification failed (project=%s): %s", project_id, exc)
        return None
    if row is None:
        return None
    meta = lqs.qualification_meta(row) or {}
    return meta.get("effective_decision") or row.decision
