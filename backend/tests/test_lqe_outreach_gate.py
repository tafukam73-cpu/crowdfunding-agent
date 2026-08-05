"""営業アウトリーチ送信前関門（PR-5）のオフライン検証。

ネットワーク不要・SQLite・mock プロバイダのみ。pytest には依存しない（§7）。

検証の重点:
  - **provider.create_draft() より前に判定**し、clear 以外は provider を呼ばない
  - review も止める（案A）。人の override だけが通す
  - fail closed（判定できなければ止める）
  - 履歴を無制限に増やさない（signals_digest ＋ 24h で再利用）
  - mark_sent は止めず、監査記録を残す
  - 409 に禁止キー・秘密情報・internal_db URL を出さない

実行: docker compose exec -T backend python tests/test_lqe_outreach_gate.py
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "lqe_outreach_gate_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"
# このファイルは **enforce 前提**の検証（observe は
# test_lqe_outreach_gate_observe.py が担当）。app.config の読み込み前に固定する。
os.environ["OUTREACH_GATE_MODE"] = "enforce"
# プロバイダは必ず mock（実 Gmail を呼ばない）。
os.environ.pop("GMAIL_CLIENT_ID", None)
os.environ.pop("GMAIL_REFRESH_TOKEN", None)

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

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
    """provider.create_draft が呼ばれたか数えるだけの mock。外部通信しない。"""

    name = "mock"

    def create_draft(self, message):
        from app.email.providers.base import DraftResult

        _calls["create_draft"] += 1
        return DraftResult(provider="mock", draft_id="d1", status="created")


def install_spy():
    _calls["create_draft"] = 0
    eds.get_email_provider = lambda: SpyProvider()  # type: ignore[assignment]


def make_project(db, *, clear: bool = False, **over) -> Project:
    """clear=True なら pre_outreach が clear になる案件を作る。"""
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
        subject="Hello",
        body="Body text",
        language="en",
        model="test",
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
#  1. clear / blocked / review と provider 呼び出し
# --------------------------------------------------------------------------- #
def test_clear_allows_draft():
    print("test_clear_allows_draft")
    db = SessionLocal()
    install_spy()
    try:
        p = make_project(db, clear=True)
        decision, _payload, _row = gate.evaluate(db, p)
        check("前提: clear になる", decision == "clear")
        d = make_draft(db, p)
        result, recipient, qualification = eds.create_provider_draft(
            db, d, "to@example.com")
        check("clear なら下書き作成が成功する", result.status == "created")
        check("provider.create_draft が 1 回呼ばれる", _calls["create_draft"] == 1)
        check("宛先が解決される", recipient == "to@example.com")
        check("EmailDraft に記録される", d.provider_draft_id == "d1")
        check("qualification 監査情報が返る", qualification["decision"] == "clear")
    finally:
        db.close()


def test_blocked_does_not_call_provider():
    print("test_blocked_does_not_call_provider")
    db = SessionLocal()
    install_spy()
    try:
        p = make_project(db)  # maker 未同定 → E で blocked
        decision, _payload, _row = gate.evaluate(db, p)
        check("前提: blocked になる", decision == "blocked")
        d = make_draft(db, p)
        before = (d.provider, d.provider_draft_id, p.sales_status, p.archived_at)
        raised = None
        try:
            eds.create_provider_draft(db, d, "to@example.com")
        except gate.LeadQualificationBlocked as exc:
            raised = exc
        check("LeadQualificationBlocked が送出される", raised is not None)
        check("provider.create_draft は呼ばれない", _calls["create_draft"] == 0)
        db.refresh(d)
        db.refresh(p)
        check("draft 状態は変わらない",
              (d.provider, d.provider_draft_id) == before[:2])
        check("sales_status は変わらない", p.sales_status == before[2])
        check("archived_at は変わらない（自動アーカイブなし）",
              p.archived_at is None and before[3] is None)
        check("payload に blocker_codes がある",
              "E" in (raised.payload.get("blocker_codes") or []))
    finally:
        db.close()


def test_review_is_blocked_too():
    print("test_review_is_blocked_too")
    db = SessionLocal()
    install_spy()
    try:
        # 公式サイト検証済み（E/S は解消）＋ 非物理の WEAK 語のみ → O が review
        p = make_project(
            db, clear=True,
            title="Daily planner app",
            description="A planner app for your routine.",
            description_clean="A planner app for your routine.",
        )
        decision, payload, _ = gate.evaluate(db, p)
        check("前提: review になる", decision == "review")
        d = make_draft(db, p)
        raised = None
        try:
            eds.create_provider_draft(db, d, "to@example.com")
        except gate.LeadQualificationBlocked as exc:
            raised = exc
        check("review も止める（案A）", raised is not None)
        check("review でも provider は呼ばれない", _calls["create_draft"] == 0)
        check("payload に review_codes がある",
              bool(raised.payload.get("review_codes")))
    finally:
        db.close()


# --------------------------------------------------------------------------- #
#  2. override
# --------------------------------------------------------------------------- #
def _do_override(db, p, *, decision="clear", url="https://evidence.example/a"):
    return lqs.record_override(
        db, p, lqs.STAGE_PRE_OUTREACH, decision,
        reason="担当者に直接確認済み", evidence_url=url,
    )


def _stamp_digest(db, p):
    """override 行に現在の signals_digest を埋める（API 経由と同じ状態にする）。"""
    row = lqs.get_latest(db, p.id, stage=lqs.STAGE_PRE_OUTREACH)
    digest = gate.signals_digest(lqs.gather_signals(db, p))
    gate._attach_digest(row, digest)
    db.commit()
    return row


def test_valid_override_allows():
    print("test_valid_override_allows")
    db = SessionLocal()
    install_spy()
    try:
        p = make_project(db)
        _do_override(db, p)
        _stamp_digest(db, p)
        decision, payload, _ = gate.evaluate(db, p)
        check("有効な override は clear として扱う", decision == "clear")
        check("overridden=true", payload["overridden"] is True)
        check("machine_decision は blocked のまま",
              payload["machine_decision"] == "blocked")
        d = make_draft(db, p)
        eds.create_provider_draft(db, d, "to@example.com")
        check("override 済みなら下書き作成できる", _calls["create_draft"] == 1)
    finally:
        db.close()


def test_override_variants_are_invalid():
    print("test_override_variants_are_invalid")
    db = SessionLocal()
    try:
        # override 無し
        p1 = make_project(db)
        check("override 無しは通さない", gate.evaluate(db, p1)[0] != "clear")

        # blocked への override は当然通さない
        p2 = make_project(db)
        _do_override(db, p2, decision="blocked")
        _stamp_digest(db, p2)
        check("blocked への override は通さない",
              gate.evaluate(db, p2)[0] != "clear")

        # 72h 超過
        p3 = make_project(db)
        _do_override(db, p3)
        row3 = _stamp_digest(db, p3)
        row3.created_at = _now() - timedelta(hours=73)
        db.commit()
        check("72h を超えた override は無効",
              not gate.valid_override(row3, gate.signals_digest(
                  lqs.gather_signals(db, p3)), now=_now()))

        # override 後に recheck → 最新は機械判定になる
        p4 = make_project(db)
        _do_override(db, p4)
        _stamp_digest(db, p4)
        lqs.run(db, p4, lqs.STAGE_PRE_OUTREACH)
        check("override 後の recheck で override は効かない",
              gate.evaluate(db, p4)[0] != "clear")

        # digest 不一致
        p5 = make_project(db)
        _do_override(db, p5)
        row5 = _stamp_digest(db, p5)
        check("digest 不一致の override は無効",
              not gate.valid_override(row5, "deadbeef", now=_now()))

        # reason 空 / evidence_url が db://
        p6 = make_project(db)
        _do_override(db, p6)
        row6 = _stamp_digest(db, p6)
        digest6 = gate.signals_digest(lqs.gather_signals(db, p6))
        row6.override_reason = "   "
        db.commit()
        check("reason 空白のみの override は無効",
              not gate.valid_override(row6, digest6, now=_now()))
        row6.override_reason = "理由あり"
        row6.override_evidence_url = "db://projects/1#x"
        db.commit()
        check("evidence_url が db:// の override は無効",
              not gate.valid_override(row6, digest6, now=_now()))

        # pre_research の override は pre_outreach に効かない
        p7 = make_project(db)
        lqs.record_override(db, p7, lqs.STAGE_PRE_RESEARCH, "clear",
                            reason="r", evidence_url="https://a.example")
        check("pre_research の override は pre_outreach に効かない",
              gate.evaluate(db, p7)[0] != "clear")
    finally:
        db.close()


# --------------------------------------------------------------------------- #
#  3. digest / 履歴の増え方
# --------------------------------------------------------------------------- #
def test_digest_properties():
    print("test_digest_properties")
    check("順序に依存しない",
          gate.signals_digest({"a": 1, "b": 2})
          == gate.signals_digest({"b": 2, "a": 1}))
    check("None と空文字を同一視する",
          gate.signals_digest({"a": None}) == gate.signals_digest({"a": ""}))
    check("日時表現を正規化する",
          gate.signals_digest({"t": datetime(2026, 1, 1, tzinfo=timezone.utc)})
          == gate.signals_digest({"t": datetime(2026, 1, 1)}))
    check("値が変われば変わる",
          gate.signals_digest({"a": 1}) != gate.signals_digest({"a": 2}))
    check("SHA-256（64桁hex）", len(gate.signals_digest({"a": 1})) == 64)
    d1 = gate.signals_digest(
        {"business_emails": [{"email": "secret@acme.example", "source_url": "u"}]})
    check("メールのローカル部を含めない", "secret" not in json.dumps(d1))


def test_history_reuse_and_growth():
    print("test_history_reuse_and_growth")
    db = SessionLocal()
    try:
        p = make_project(db)
        check("初期は履歴 0", history_count(db, p.id) == 0)
        gate.evaluate(db, p)
        check("初回で 1 行追加", history_count(db, p.id) == 1)
        for _ in range(5):
            gate.evaluate(db, p)
        check("同一入力の連打では履歴が増えない（再利用）",
              history_count(db, p.id) == 1)

        # 入力を変える（公式サイトが取れた）→ 1 行だけ増える
        db.add(ContactDiscovery(
            project_id=p.id, v2_official_site_url="https://new.example",
            v2_official_site_source="search",
            v2_primary_source_url="https://new.example/c",
            v2_researched_at=_now()))
        db.commit()
        gate.evaluate(db, p)
        check("入力が変われば 1 行追加", history_count(db, p.id) == 2)
        gate.evaluate(db, p)
        check("再び連打しても増えない", history_count(db, p.id) == 2)

        # 24h 超過なら再判定
        row = lqs.get_latest(db, p.id, stage=lqs.STAGE_PRE_OUTREACH)
        row.created_at = _now() - timedelta(hours=25)
        db.commit()
        gate.evaluate(db, p)
        check("24h を超えたら再判定して 1 行追加", history_count(db, p.id) == 3)

        digest = gate.signals_digest(lqs.gather_signals(db, p))
        latest = lqs.get_latest(db, p.id, stage=lqs.STAGE_PRE_OUTREACH)
        check("digest が予約メタに保存される",
              (lqs.qualification_meta(latest) or {}).get("signals_digest") == digest)
        check("digest は通常 Finding として数えない",
              len(lqs.findings_of(latest)) == 20)
        machine = lqs.qualify(lqs.gather_signals(db, p), lqs.STAGE_PRE_OUTREACH)
        check("digest は evidence_count に加算しない",
              latest.evidence_count == machine.evidence_count)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
#  4. fail closed
# --------------------------------------------------------------------------- #
def test_fail_closed():
    print("test_fail_closed")
    db = SessionLocal()
    install_spy()
    original = lqs.gather_signals
    try:
        p = make_project(db, clear=True)
        d = make_draft(db, p)

        def boom(*a, **k):
            raise RuntimeError("LQE broken")

        lqs.gather_signals = boom
        raised = None
        try:
            eds.create_provider_draft(db, d, "to@example.com")
        except gate.LeadQualificationBlocked as exc:
            raised = exc
        finally:
            lqs.gather_signals = original
        check("LQE 障害時は fail closed で止める", raised is not None)
        check("provider は呼ばれない", _calls["create_draft"] == 0)
        check("メッセージが専用のものになる",
              raised.message == gate.MESSAGE_UNAVAILABLE)
        check("payload の decision は None", raised.payload["decision"] is None)
        check("内部例外文言を payload に出さない",
              "LQE broken" not in json.dumps(raised.payload, ensure_ascii=False))
        check("stack trace を出さない",
              "Traceback" not in json.dumps(raised.payload, ensure_ascii=False))
    finally:
        lqs.gather_signals = original
        db.close()


# --------------------------------------------------------------------------- #
#  5. 409 payload の安全性
# --------------------------------------------------------------------------- #
def test_409_payload_safety():
    print("test_409_payload_safety")
    db = SessionLocal()
    try:
        p = make_project(db)
        _decision, payload, _ = gate.evaluate(db, p)
        detail = {"message": gate.MESSAGE_BLOCKED, "qualification": payload}
        text = json.dumps(detail, ensure_ascii=False)

        expected = {"stage", "decision", "machine_decision", "effective_decision",
                    "overridden", "blocker_codes", "review_codes", "reasons",
                    "checked_at", "persisted"}
        check("qualification のキー集合が設計どおり", set(payload) == expected)
        bad = [k for _p, k, _v in walk(detail)
               if any(b in k.lower() for b in BANNED_KEYS)]
        check(f"禁止キーが無い（{bad[:3]}）", bad == [])
        check("internal_db の URL を出さない", "db://" not in text)
        check("http の証跡 URL も出さない（Evidence 本文を返さない）",
              "http://" not in text and "https://" not in text)
        check("メールアドレスを出さない", "@" not in text)
        for word in ("返信率", "成功率", "成功確率", "可能性スコア", "予測"):
            check(f"'{word}' を含まない", word not in text)
        check("evidence キー自体が無い", "evidence" not in text)
    finally:
        db.close()


# --------------------------------------------------------------------------- #
#  6. mark_sent
# --------------------------------------------------------------------------- #
def test_mark_sent_not_blocked():
    print("test_mark_sent_not_blocked")
    db = SessionLocal()
    try:
        p = make_project(db)
        sos.get_or_create(db, p)
        row = sos.get_by_project(db, p.id)
        row.generated_subject = "S"
        row.generated_body = "B"
        row.generated_language = "en"
        db.commit()
        check("前提: blocked", gate.evaluate(db, p)[0] == "blocked")

        out = sos.mark_sent(db, p, language="en")
        check("blocked でも mark_sent は成功する", out["already_sent"] is False)
        check("outreach_status=sent", out["outreach"].outreach_status == "sent")
        check("sent_at が入る", out["outreach"].sent_at is not None)
        check("followup_due_at が設定される",
              out["outreach"].followup_due_at is not None)
        db.refresh(p)
        check("sales_status が contacted へ自動同期される",
              p.sales_status == "contacted")
        check("archived_at は変わらない", p.archived_at is None)

        from app.models.crm import SalesActivity

        notes = [a.summary or "" for a in db.query(SalesActivity).all()]
        audit = [n for n in notes if "LQE監査" in n]
        check("監査記録が timeline に残る", len(audit) >= 1)
        text = " ".join(audit)
        check("監査に decision が入る", "decision=blocked" in text)
        check("監査に stage が入る", "stage=pre_outreach" in text)
        check("監査に blocker_codes が入る", "blocker_codes=" in text)
        check("監査にメールアドレスを書かない", "@" not in text)
        check("監査に証跡 URL を書かない", "http" not in text and "db://" not in text)
    finally:
        db.close()


def test_mark_sent_audit_failure_is_tolerated():
    print("test_mark_sent_audit_failure_is_tolerated")
    db = SessionLocal()
    original = gate.audit_note
    try:
        p = make_project(db)
        sos.get_or_create(db, p)
        row = sos.get_by_project(db, p.id)
        row.generated_subject = "S"
        row.generated_body = "B"
        db.commit()

        def boom(*a, **k):
            raise RuntimeError("audit broken")

        gate.audit_note = boom
        out = sos.mark_sent(db, p, language="en")
        gate.audit_note = original
        check("監査に失敗しても mark_sent は成功する", out["already_sent"] is False)
        from app.models.crm import SalesActivity

        notes = " ".join(a.summary or "" for a in db.query(SalesActivity).all())
        check("qualification_unavailable が記録される",
              gate.AUDIT_UNAVAILABLE in notes)
    finally:
        gate.audit_note = original
        db.close()


# --------------------------------------------------------------------------- #
#  7. compose URL / 生成系 / 副作用
# --------------------------------------------------------------------------- #
def test_compose_url_policy():
    print("test_compose_url_policy")
    db = SessionLocal()
    try:
        blocked_p = make_project(db)
        sos.get_or_create(db, blocked_p)
        r = sos.get_by_project(db, blocked_p.id)
        r.generated_subject = "S"; r.generated_body = "B"; db.commit()
        before = history_count(db, blocked_p.id)
        ser = sos.serialize(db, r)
        check("blocked では compose URL を出さない",
              ser["gmail_compose_url"] is None)
        check("判定値を返して理由を説明できる",
              ser["qualification_decision"] in (None, "blocked"))
        check("serialize は履歴を増やさない",
              history_count(db, blocked_p.id) == before)

        clear_p = make_project(db, clear=True)
        sos.get_or_create(db, clear_p)
        r2 = sos.get_by_project(db, clear_p.id)
        r2.generated_subject = "S"; r2.generated_body = "B"; db.commit()
        gate.evaluate(db, clear_p)  # 判定を保存
        ser2 = sos.serialize(db, r2)
        check("clear なら compose URL を出す", bool(ser2["gmail_compose_url"]))
        check("compose URL は Gmail 作成画面",
              ser2["gmail_compose_url"].startswith("https://mail.google.com/mail/"))
    finally:
        db.close()


def test_generation_not_blocked():
    print("test_generation_not_blocked")
    db = SessionLocal()
    try:
        p = make_project(db)
        check("前提: blocked", gate.evaluate(db, p)[0] == "blocked")
        row = sos.get_or_create(db, p)
        check("下書き行の作成は止めない", row is not None)
        ok, _reason = sos.followup_eligibility(row)
        check("followup 判定関数は例外を出さない", isinstance(ok, bool))
        check("生成系に関門を入れていない（今回対象外）",
              "require_clear" not in Path(
                  BACKEND / "app" / "services" / "sales_outreach_service.py"
              ).read_text(encoding="utf-8"))
    finally:
        db.close()


def test_no_network_and_no_real_provider():
    print("test_no_network_and_no_real_provider")

    def boom(*a, **k):
        raise AssertionError("network access attempted")

    db = SessionLocal()
    install_spy()
    orig = (socket.socket.connect, socket.socket.connect_ex,
            urllib.request.urlopen, socket.getaddrinfo)
    try:
        p = make_project(db, clear=True)
        d = make_draft(db, p)
        socket.socket.connect = boom
        socket.socket.connect_ex = boom
        urllib.request.urlopen = boom
        socket.getaddrinfo = boom
        try:
            gate.evaluate(db, p)
            eds.create_provider_draft(db, d, "to@example.com")
            ok = True
        except AssertionError:
            ok = False
        finally:
            (socket.socket.connect, socket.socket.connect_ex,
             urllib.request.urlopen, socket.getaddrinfo) = orig
        check("関門も下書き作成もネットワークに触れない（mock）", ok)
        src = Path(BACKEND / "app" / "services"
                   / "outreach_qualification_gate.py").read_text(encoding="utf-8")
        for banned in ("httpx", "requests.", "urllib.request", "playwright"):
            check(f"関門ソースに {banned} を含まない", banned not in src)
    finally:
        db.close()


def test_gate_runs_before_provider():
    """関門が provider 呼び出しより前にあることをコード順で固定する。

    docstring の言及に引きずられないよう、**関数本体のソース**だけを見る。
    """
    print("test_gate_runs_before_provider")
    import inspect

    fn = eds.create_provider_draft
    body = inspect.getsource(fn).replace(fn.__doc__ or "", "")
    i_gate = body.find("require_clear")
    i_provider = body.find("provider.create_draft")
    i_get = body.find("get_email_provider()")
    check("関門が本体に存在する", i_gate >= 0)
    check("require_clear が provider.create_draft より前にある",
          0 <= i_gate < i_provider)
    check("get_email_provider の取得より前にある", 0 <= i_gate < i_get)


def main():
    test_clear_allows_draft()
    test_blocked_does_not_call_provider()
    test_review_is_blocked_too()
    test_valid_override_allows()
    test_override_variants_are_invalid()
    test_digest_properties()
    test_history_reuse_and_growth()
    test_fail_closed()
    test_409_payload_safety()
    test_mark_sent_not_blocked()
    test_mark_sent_audit_failure_is_tolerated()
    test_compose_url_policy()
    test_generation_not_blocked()
    test_no_network_and_no_real_provider()
    test_gate_runs_before_provider()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
