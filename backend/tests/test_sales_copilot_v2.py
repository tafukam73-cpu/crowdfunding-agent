"""Sales Copilot v2 統合ロジック（combine_v2）のオフライン検証。

DB 非依存の純粋関数 combine_v2() が、v1 カード＋3 スコア＋連絡先から妥当な優先度・
判断・次アクションを導くことを検証する。

実行（backend ディレクトリで）:
    python tests/test_sales_copilot_v2.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import sales_copilot_v2_service as v2  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def _assessment(jm, ex, mk, overall, conf=70):
    return {
        "japan_market_fit": {"score": jm, "level": "high", "reasons": ["r"]},
        "exclusivity": {"score": ex, "level": "high", "reasons": ["r"]},
        "makuake_fit": {"score": mk, "level": "high", "reasons": ["r"]},
        "overall_priority_score": overall,
        "confidence": conf,
    }


def _card(decision="needs_email", next_action="v1 next", reasons=None, email=None):
    return {
        "decision": decision, "next_action": next_action,
        "reasons": reasons or ["v1 reason"], "recommended_email": email,
        "summary": {"contact_status": "メール取得済み" if email else "連絡先未取得"},
    }


def test_high_potential_with_contact_sells_now():
    print("test_high_potential_with_contact_sells_now")
    rec = v2.combine_v2(
        _card(email="x@brand.com"), _assessment(70, 75, 70, 73),
        {"has_email": True, "has_form": False},
    )
    check("独占×市場適性×連絡先 → sell_now_exclusive", rec["decision"] == "sell_now_exclusive")
    check("独占営業メールを提案", "独占" in rec["next_action"])
    check("優先度 high", rec["priority_label"] == "high")
    check("タグに独占好機", "独占販売の好機" in rec["tags"])


def test_high_potential_no_contact_needs_contact():
    print("test_high_potential_no_contact_needs_contact")
    rec = v2.combine_v2(
        _card(decision="needs_contact"), _assessment(65, 70, 60, 66),
        {"has_email": False, "has_form": False},
    )
    check("有望だが連絡先なし → needs_contact", rec["decision"] == "needs_contact")
    check("Contact Intelligence を促す", "Contact Intelligence" in rec["next_action"])


def test_low_potential_deprioritized():
    print("test_low_potential_deprioritized")
    rec = v2.combine_v2(
        _card(), _assessment(30, 25, 20, 25, conf=60),
        {"has_email": False, "has_form": False},
    )
    check("低適性 → deprioritize", rec["decision"] == "deprioritize")
    check("優先度 low", rec["priority_label"] == "low")


def test_drop_respected():
    print("test_drop_respected")
    rec = v2.combine_v2(
        _card(decision="drop", reasons=["日本に代理店あり"]),
        _assessment(80, 80, 80, 80), {"has_email": True, "has_form": False},
    )
    check("v1 の drop を尊重", rec["decision"] == "drop")
    check("drop は優先度が下がる", rec["priority_score"] < 80)


def test_closed_respected():
    print("test_closed_respected")
    rec = v2.combine_v2(
        _card(decision="closed", reasons=["成約済み"]),
        _assessment(70, 70, 70, 70), {"has_email": True},
    )
    check("v1 の closed を尊重", rec["decision"] == "closed")


def test_engaged_states_preserved():
    print("test_engaged_states_preserved")
    # 既に接触/商談中の案件はスコアが高くても「今すぐ営業」で上書きしない
    for st in ("waiting", "needs_followup", "needs_negotiation"):
        rec = v2.combine_v2(
            _card(decision=st, next_action="返信を待つ", email="a@b.com"),
            _assessment(80, 80, 80, 80), {"has_email": True, "has_form": False},
        )
        check(f"v1 {st} を尊重（sell_now で上書きしない）", rec["decision"] == st)


def test_low_confidence_tag():
    print("test_low_confidence_tag")
    rec = v2.combine_v2(
        _card(), _assessment(50, 50, 50, 50, conf=30),
        {"has_email": False, "has_form": False},
    )
    check("低 confidence でデータ不足タグ", "データ不足（要追加調査）" in rec["tags"])


def test_priority_monotonic():
    print("test_priority_monotonic")
    hi = v2.combine_v2(_card(email="a@b.com"), _assessment(80, 80, 80, 80),
                       {"has_email": True})
    lo = v2.combine_v2(_card(), _assessment(30, 30, 30, 30, conf=60),
                       {"has_email": False})
    check("高適性の優先度 > 低適性", hi["priority_score"] > lo["priority_score"])
    check("連絡先ありで加点", hi["priority_score"] >= 80)


def test_makuake_mentioned_when_high():
    print("test_makuake_mentioned_when_high")
    rec = v2.combine_v2(_card(email="a@b.com"), _assessment(60, 65, 70, 65),
                        {"has_email": True})
    check("Makuake 高で次アクションに言及", "Makuake" in rec["next_action"])
    check("Makuake タグ", "Makuake再ローンチ有望" in rec["tags"])


if __name__ == "__main__":
    test_high_potential_with_contact_sells_now()
    test_high_potential_no_contact_needs_contact()
    test_low_potential_deprioritized()
    test_drop_respected()
    test_closed_respected()
    test_engaged_states_preserved()
    test_low_confidence_tag()
    test_priority_monotonic()
    test_makuake_mentioned_when_high()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
