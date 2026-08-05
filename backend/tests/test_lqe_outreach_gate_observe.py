"""送信前関門の observe モード（段階導入）のオフライン検証。

ネットワーク不要・SQLite・mock プロバイダのみ。pytest には依存しない（§7）。

observe は「判定・履歴保存・警告だけ行い、業務を止めない」モード。
enforce 側の検証は test_lqe_outreach_gate.py が担当する。

検証の重点:
  - **未設定・空文字・不正値はすべて observe に丸まる**（本番設定の欠落を含む）
  - observe では blocked / review / 判定不能でも provider を呼ぶ
  - **observe でも判定と履歴保存は省略しない**（判定不能も隠さない）
  - 監査 payload に禁止キー・秘密情報・internal_db URL を出さない
  - observe → enforce の切替で同一案件が 200 → 409 になる

実行: docker compose exec -T backend python tests/test_lqe_outreach_gate_observe.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "lqe_gate_observe_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"
# **既定値の検証のため、環境変数を明示的に外した状態で起動する。**
os.environ.pop("OUTREACH_GATE_MODE", None)
os.environ.pop("GMAIL_CLIENT_ID", None)
os.environ.pop("GMAIL_REFRESH_TOKEN", None)

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.email_draft import EmailDraft, EmailType  # noqa: E402
from app.models.lead_qualification import LeadQualification  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services import email_delivery_service as eds  # noqa: E402
from app.services import lead_qualification_service as lqs  # noqa: E402
from app.services import outreach_qualification_gate as gate  # noqa: E402
from app.services import sales_outreach_service as sos  # noqa: E402

Base.metadata.create_all(engine)

_passed = _failed = 0
_seq = [0]
_calls = {"create_draft": 0}

BANNED_KEYS = ("score", "probability", "forecast", "reply_rate", "success_rate",
               "makuake_fit", "japan_crowdfunding", "confidence", "stars", "percent")


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def _now():
    return datetime.now(timezone.utc)


class SpyProvider:
    name = "mock"

    def create_draft(self, message):
        from app.email.providers.base import DraftResult

        _calls["create_draft"] += 1
        return DraftResult(provider="mock", draft_id="d1", status="created")


def install_spy():
    _calls["create_draft"] = 0
    eds.get_email_provider = lambda: SpyProvider()  # type: ignore[assignment]


def set_mode(value):
    """settings 経由でモードを差し替える（current_mode は毎回読み直す）。"""
    settings.outreach_gate_mode = value


def make_project(db, *, clear: bool = False, **over) -> Project:
    _seq[0] += 1
    n = _seq[0]
    data = {
        "title": "Compact Stainless Steel Water Bottle",
        "source_site": "kickstarter",
        "source_url": f"https://www.kickstarter.com/projects/acme/bottle-{n}",
        "category": "kitchen",
        "description": (
            "A rechargeable stainless steel bottle with a companion app. "
            "Waterproof compact design solves the daily hydration problem."
        ),
        "description_clean": (
            "A rechargeable stainless steel bottle with a companion app. "
            "Waterproof compact design solves the daily hydration problem."
        ),
        "maker_name": "Acme Studio",
        "currency": "USD",
    }
    data.update(over)
    p = Project(**data)
    db.add(p)
    db.commit()
    db.refresh(p)
    if clear:
        db.add(ContactDiscovery(
            project_id=p.id,
            v2_official_site_url=f"https://acme-{n}.example",
            v2_official_site_source="project_website",
            v2_primary_source_url=f"https://acme-{n}.example/contact",
            v2_researched_at=_now() - timedelta(days=1),
        ))
        db.commit()
    return p


def make_draft(db, project) -> EmailDraft:
    d = EmailDraft(
        project_id=project.id,
        email_type=EmailType.initial_outreach.value,
        subject="Hello", body="Body text", language="en", model="test",
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def history_count(db, project_id: int) -> int:
    return db.query(LeadQualification).filter_by(project_id=project_id).count()


def walk(node, path="$"):
    if isinstance(node, dict):
        for k, v in node.items():
            yield path, k, v
            yield from walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")


# --------------------------------------------------------------------------- #
#  1. モードの解決
# --------------------------------------------------------------------------- #
def test_mode_resolution():
    print("test_mode_resolution")
    check("環境変数 未設定なら既定は observe",
          settings.outreach_gate_mode == "observe")
    check("current_mode() の既定は observe", gate.current_mode() == "observe")
    check("既定では enforce していない", gate.is_enforcing() is False)

    original = settings.outreach_gate_mode
    try:
        for bad in ("", "   ", "yes", "ENFORCED", "true", "1", "off", None):
            set_mode(bad)
            check(f"不正値 {bad!r} は observe に丸まる",
                  gate.current_mode() == "observe" and not gate.is_enforcing())
        set_mode("enforce")
        check("enforce は明示値のときだけ有効",
              gate.current_mode() == "enforce" and gate.is_enforcing() is True)
        set_mode("ENFORCE")
        check("大文字 ENFORCE も許容する（正規化）", gate.is_enforcing() is True)
        set_mode(" enforce ")
        check("前後空白つきでも許容する", gate.is_enforcing() is True)
        set_mode("observe")
        check("observe は observe", gate.current_mode() == "observe")
        check("MODES は 2 種", set(gate.MODES) == {"observe", "enforce"})
    finally:
        set_mode(original)


# --------------------------------------------------------------------------- #
#  2. observe では止めない
# --------------------------------------------------------------------------- #
def _observe_creates_draft(db, p, label):
    install_spy()
    d = make_draft(db, p)
    result, recipient, qualification = eds.create_provider_draft(
        db, d, "to@example.com")
    check(f"{label}: provider.create_draft が呼ばれる", _calls["create_draft"] == 1)
    check(f"{label}: 下書きが作成される", result.status == "created")
    check(f"{label}: 宛先が返る", recipient == "to@example.com")
    return qualification


def test_observe_allows_blocked():
    print("test_observe_allows_blocked")
    set_mode("observe")
    db = SessionLocal()
    try:
        p = make_project(db)
        decision, _payload, _ = gate.evaluate(db, p)
        check("前提: blocked", decision == "blocked")
        q = _observe_creates_draft(db, p, "blocked")
        check("監査 payload の decision が blocked", q["decision"] == "blocked")
        check("blocker_codes が返る", "E" in (q["blocker_codes"] or []))
        check("persisted=true（記録済み）", q["persisted"] is True)
    finally:
        db.close()


def test_observe_allows_review():
    print("test_observe_allows_review")
    set_mode("observe")
    db = SessionLocal()
    try:
        p = make_project(
            db, clear=True,
            title="Daily planner app",
            description="A planner app for your routine.",
            description_clean="A planner app for your routine.",
        )
        decision, _payload, _ = gate.evaluate(db, p)
        check("前提: review", decision == "review")
        q = _observe_creates_draft(db, p, "review")
        check("監査 payload の decision が review", q["decision"] == "review")
        check("review_codes が返る", bool(q["review_codes"]))
    finally:
        db.close()


def test_observe_allows_unavailable():
    print("test_observe_allows_unavailable")
    set_mode("observe")
    db = SessionLocal()
    original = lqs.gather_signals
    try:
        p = make_project(db, clear=True)
        d = make_draft(db, p)
        install_spy()

        def boom(*a, **k):
            raise RuntimeError("LQE broken")

        lqs.gather_signals = boom
        result, _recipient, q = eds.create_provider_draft(db, d, "to@example.com")
        lqs.gather_signals = original

        check("判定不能でも下書きは作られる", result.status == "created")
        check("provider.create_draft が呼ばれる", _calls["create_draft"] == 1)
        check("判定不能を隠さない（decision=None）", q["decision"] is None)
        check("persisted=false で未保存を明示", q["persisted"] is False)
        check("内部例外文言を出さない",
              "LQE broken" not in json.dumps(q, ensure_ascii=False))
    finally:
        lqs.gather_signals = original
        db.close()


def test_observe_still_records_history():
    print("test_observe_still_records_history")
    set_mode("observe")
    db = SessionLocal()
    try:
        p = make_project(db)
        check("初期は履歴 0", history_count(db, p.id) == 0)
        d = make_draft(db, p)
        install_spy()
        eds.create_provider_draft(db, d, "to@example.com")
        check("observe でも判定履歴が保存される", history_count(db, p.id) == 1)
        row = lqs.get_latest(db, p.id, stage=lqs.STAGE_PRE_OUTREACH)
        check("保存された stage が pre_outreach", row.stage == "pre_outreach")
        check("digest も保存される",
              bool((lqs.qualification_meta(row) or {}).get("signals_digest")))

        d2 = make_draft(db, p)
        eds.create_provider_draft(db, d2, "to@example.com")
        check("同一入力の再実行では履歴が増えない（再利用）",
              history_count(db, p.id) == 1)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
#  3. 監査 payload の安全性
# --------------------------------------------------------------------------- #
def test_observe_audit_payload_safety():
    print("test_observe_audit_payload_safety")
    set_mode("observe")
    db = SessionLocal()
    try:
        p = make_project(db)
        d = make_draft(db, p)
        install_spy()
        _result, _recipient, q = eds.create_provider_draft(db, d, "to@example.com")

        expected = {"stage", "decision", "machine_decision", "effective_decision",
                    "overridden", "blocker_codes", "review_codes", "reasons",
                    "checked_at", "persisted"}
        check("監査 payload のキー集合が設計どおり", set(q) == expected)
        text = json.dumps(q, ensure_ascii=False)
        bad = [k for _p, k, _v in walk(q)
               if any(b in k.lower() for b in BANNED_KEYS)]
        check(f"禁止キーが無い（{bad[:3]}）", bad == [])
        check("internal_db の URL を出さない", "db://" not in text)
        check("http(s) の証跡 URL を出さない",
              "http://" not in text and "https://" not in text)
        check("メールアドレスを出さない", "@" not in text)
        for word in ("返信率", "成功率", "成功確率", "可能性スコア", "予測"):
            check(f"'{word}' を含まない", word not in text)

        from app.schemas.email_draft import QualificationAudit

        audit = QualificationAudit(**q)
        check("スキーマに載せられる", audit.stage == "pre_outreach")
        check("スキーマ経由でも禁止キーが増えない",
              set(audit.model_dump()) == expected)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
#  4. Compose URL / mark_sent
# --------------------------------------------------------------------------- #
def test_observe_compose_url():
    print("test_observe_compose_url")
    db = SessionLocal()
    try:
        p = make_project(db)
        sos.get_or_create(db, p)
        r = sos.get_by_project(db, p.id)
        r.generated_subject = "S"; r.generated_body = "B"; db.commit()
        gate.evaluate(db, p)  # blocked を保存
        before = history_count(db, p.id)

        set_mode("observe")
        ser = sos.serialize(db, r)
        check("observe では blocked でも compose URL を返す",
              bool(ser["gmail_compose_url"]))
        check("判定値も返す", ser["qualification_decision"] == "blocked")
        check("本文プレビューは常に返る",
              ser["generated_subject"] == "S" and ser["generated_body"] == "B")
        check("serialize は履歴を増やさない", history_count(db, p.id) == before)

        set_mode("enforce")
        ser2 = sos.serialize(db, r)
        check("enforce では blocked で compose URL を出さない",
              ser2["gmail_compose_url"] is None)
        check("enforce でも本文プレビューは返る",
              ser2["generated_subject"] == "S")
        check("enforce でも履歴を増やさない", history_count(db, p.id) == before)
        set_mode("observe")

        check("allows_compose_url: observe は常に True",
              gate.allows_compose_url("blocked") is True)
        set_mode("enforce")
        check("allows_compose_url: enforce は clear のみ",
              gate.allows_compose_url("clear") is True
              and gate.allows_compose_url("review") is False
              and gate.allows_compose_url(None) is False)
        set_mode("observe")
    finally:
        set_mode("observe")
        db.close()


def test_observe_mark_sent_audit_unchanged():
    print("test_observe_mark_sent_audit_unchanged")
    set_mode("observe")
    db = SessionLocal()
    try:
        p = make_project(db)
        sos.get_or_create(db, p)
        r = sos.get_by_project(db, p.id)
        r.generated_subject = "S"; r.generated_body = "B"
        r.generated_language = "en"; db.commit()

        out = sos.mark_sent(db, p, language="en")
        check("observe でも mark_sent は成功する", out["already_sent"] is False)
        check("outreach_status=sent", out["outreach"].outreach_status == "sent")
        check("followup_due_at を維持", out["outreach"].followup_due_at is not None)
        db.refresh(p)
        check("sales_status contacted 同期を維持", p.sales_status == "contacted")
        check("archived_at は変わらない（自動アーカイブなし）", p.archived_at is None)

        from app.models.crm import SalesActivity

        notes = " ".join(a.summary or "" for a in db.query(SalesActivity).all())
        check("LQE 監査が timeline に残る", "LQE監査" in notes)
        check("監査に decision が入る", "decision=" in notes)
        check("監査にメールアドレスを書かない", "@" not in notes)
        check("監査に証跡 URL を書かない",
              "http" not in notes and "db://" not in notes)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
#  5. observe → enforce の切替
# --------------------------------------------------------------------------- #
def test_mode_switch_changes_behaviour():
    print("test_mode_switch_changes_behaviour")
    db = SessionLocal()
    try:
        p = make_project(db)  # blocked になる案件

        set_mode("observe")
        install_spy()
        d1 = make_draft(db, p)
        result, _rec, _q = eds.create_provider_draft(db, d1, "to@example.com")
        check("observe では作成できる（200 相当）", result.status == "created")
        check("provider が呼ばれる", _calls["create_draft"] == 1)

        set_mode("enforce")
        install_spy()
        d2 = make_draft(db, p)
        raised = None
        try:
            eds.create_provider_draft(db, d2, "to@example.com")
        except gate.LeadQualificationBlocked as exc:
            raised = exc
        check("enforce では同一案件が 409 相当で止まる", raised is not None)
        check("enforce では provider が呼ばれない", _calls["create_draft"] == 0)
        db.refresh(d2)
        check("enforce では draft 状態も変わらない", d2.provider_draft_id is None)
    finally:
        set_mode("observe")
        db.close()


# --------------------------------------------------------------------------- #
#  6. provider 情報 API / 自動操作の禁止
# --------------------------------------------------------------------------- #
def test_provider_info_exposes_mode():
    print("test_provider_info_exposes_mode")
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    set_mode("observe")
    body = client.get("/email/provider").json()
    check("provider 情報 API が 200", "provider" in body)
    check("outreach_gate_mode を返す", body.get("outreach_gate_mode") == "observe")
    set_mode("enforce")
    check("enforce も反映される",
          client.get("/email/provider").json()["outreach_gate_mode"] == "enforce")
    set_mode("bogus")
    check("不正値でも observe として返す",
          client.get("/email/provider").json()["outreach_gate_mode"] == "observe")
    set_mode("observe")

    # 案件単位のレスポンスにグローバル mode を重複させない。
    db = SessionLocal()
    try:
        p = make_project(db)
        sos.get_or_create(db, p)
        ser = sos.serialize(db, sos.get_by_project(db, p.id))
        check("案件単位レスポンスに mode を載せない",
              "outreach_gate_mode" not in ser)
    finally:
        db.close()


def test_no_auto_override_or_archive():
    print("test_no_auto_override_or_archive")
    set_mode("observe")
    db = SessionLocal()
    try:
        p = make_project(db)
        d = make_draft(db, p)
        install_spy()
        eds.create_provider_draft(db, d, "to@example.com")
        rows = lqs.list_history(db, p.id)
        check("自動 override を作らない",
              all(not (lqs.qualification_meta(r) or {}).get("overridden")
                  for r in rows))
        check("override 列は空のまま",
              all(r.override_reason is None and r.override_evidence_url is None
                  for r in rows))
        db.refresh(p)
        check("自動アーカイブしない",
              p.archived_at is None and p.archive_reason is None)

        gate_src = Path(BACKEND / "app" / "services"
                        / "outreach_qualification_gate.py").read_text(encoding="utf-8")
        check("関門は record_override を呼ばない",
              "record_override" not in gate_src)
        check("関門は archived_at に触れない", "archived_at" not in gate_src)
    finally:
        db.close()


def test_no_frontend_change():
    print("test_no_frontend_change")
    api_ts = BACKEND.parent / "frontend" / "lib" / "api.ts"
    if api_ts.exists():
        text = api_ts.read_text(encoding="utf-8")
        check("frontend に outreach_gate_mode を追加していない",
              "outreach_gate_mode" not in text)
        check("frontend に qualification_decision を追加していない",
              "qualification_decision" not in text)


def main():
    test_mode_resolution()
    test_observe_allows_blocked()
    test_observe_allows_review()
    test_observe_allows_unavailable()
    test_observe_still_records_history()
    test_observe_audit_payload_safety()
    test_observe_compose_url()
    test_observe_mark_sent_audit_unchanged()
    test_mode_switch_changes_behaviour()
    test_provider_info_exposes_mode()
    test_no_auto_override_or_archive()
    test_no_frontend_change()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
