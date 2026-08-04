"""email_role のラベル優先判定の検証。

Contact ページはアドレスの用途をラベルで明示することが多い。
  例: "Press & Influencers  ethan@relodin.com" / "Reseller  sales@relodin.com"
local-part の見た目（"ethan" → 人物）ではなく、**ラベルが示す実際の機能**を
優先しないと、広報窓口へ提携提案を送ってしまう。
gold 案件をハードコードせず一般ルールを検証する。

実行: docker compose exec -T backend python tests/test_email_role_label.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / 'email_role_label.sqlite'}"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import source_ownership as so  # noqa: E402

_passed = _failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def test_label_overrides_local_part():
    """ラベルが local-part 推論より優先されること。"""
    print("test_label_overrides_local_part")
    # 人物名アドレスだが広報窓口 → 送ってはいけない
    check("人物名 + 'Press & Influencers' → exclude",
          so.email_role("ethan@example-brand.com", label="Press & Influencers") == "exclude")
    # 人物名アドレスだが代理店窓口 → 最優先
    check("人物名 + 'Reseller' → high",
          so.email_role("ethan@example-brand.com", label="Reseller") == "high")
    # 汎用アドレスだがサポート窓口 → support に降格
    check("'hello@' + 'Customer Support' → support",
          so.email_role("hello@example-brand.com", label="Customer Support") == "support")
    # 汎用アドレスだが代理店窓口 → high に昇格
    check("'hello@' + 'Distributor Inquiries' → high",
          so.email_role("hello@example-brand.com", label="Distributor Inquiries") == "high")


def test_label_absent_falls_back():
    """ラベルが無い/判定不能なら従来の local-part 判定になること（後方互換）。"""
    print("test_label_absent_falls_back")
    check("label なし: sales@ → high", so.email_role("sales@x.com") == "high")
    check("label なし: hello@ → mid", so.email_role("hello@x.com") == "mid")
    check("label なし: support@ → support", so.email_role("support@x.com") == "support")
    check("label なし: ethan@ → person", so.email_role("ethan@x.com") == "person")
    check("label なし: noreply@ → exclude", so.email_role("noreply@x.com") == "exclude")
    check("label=None は従来どおり", so.email_role("sales@x.com", label=None) == "high")
    check("無関係ラベルは無視", so.email_role("sales@x.com", label="Our Office") == "high")


def test_exclude_wins_on_mixed_label():
    """複合ラベルは保守的に除外側へ倒すこと（送らない方に倒す）。"""
    print("test_exclude_wins_on_mixed_label")
    check("'Media & Sales' → exclude",
          so.email_role("x@y.com", label="Media & Sales") == "exclude")
    check("'Press / Partnership' → exclude",
          so.email_role("x@y.com", label="Press / Partnership") == "exclude")


def test_japanese_labels():
    print("test_japanese_labels")
    check("'代理店お問い合わせ' → high", so.email_role("a@b.com", label="代理店お問い合わせ") == "high")
    check("'広報・取材' → exclude", so.email_role("a@b.com", label="広報・取材") == "exclude")
    check("'カスタマーサポート' → support", so.email_role("a@b.com", label="カスタマーサポート") == "support")
    check("'お問い合わせ' → mid", so.email_role("a@b.com", label="お問い合わせ") == "mid")


def test_press_local_is_excluded():
    """ラベルが無くても press@ 等は除外すること。"""
    print("test_press_local_is_excluded")
    for local in ("press", "media", "publicity", "influencer"):
        check(f"{local}@ → exclude", so.email_role(f"{local}@x.com") == "exclude")


def test_role_from_label():
    print("test_role_from_label")
    check("None → None", so.role_from_label(None) is None)
    check("空文字 → None", so.role_from_label("") is None)
    check("判定不能 → None", so.role_from_label("Our Office Address") is None)
    check("'Wholesale' → high", so.role_from_label("Wholesale") == "high")


def test_label_near_email():
    """本文からアドレス直前のラベルを抽出できること。"""
    print("test_label_near_email")
    text = ("Contact Home Contact Call us (254) 454-4848 Send message hello@relodin.com "
            "Press & Influencers ethan@relodin.com Reseller sales@relodin.com Contact form")
    l_hello = so.label_near_email(text, "hello@relodin.com")
    l_ethan = so.label_near_email(text, "ethan@relodin.com")
    l_sales = so.label_near_email(text, "sales@relodin.com")
    print(f"      hello -> {l_hello!r}")
    print(f"      ethan -> {l_ethan!r}")
    print(f"      sales -> {l_sales!r}")
    check("ethan のラベルに Press が含まれる", "press" in (l_ethan or "").lower())
    check("sales のラベルに Reseller が含まれる", "reseller" in (l_sales or "").lower())
    check("ethan は exclude と判定される", so.email_role("ethan@relodin.com", label=l_ethan) == "exclude")
    check("sales は high と判定される", so.email_role("sales@relodin.com", label=l_sales) == "high")
    check("存在しないアドレスは None", so.label_near_email(text, "nobody@nowhere.com") is None)


def test_rank_ordering_unchanged():
    print("test_rank_ordering_unchanged")
    r = so._ROLE_RANK
    check("high が最優先", r["high"] == 0)
    check("exclude が最下位", r["exclude"] == 9)
    check("high < person < mid < support", r["high"] < r["person"] < r["mid"] < r["support"])


def main():
    test_label_overrides_local_part()
    test_label_absent_falls_back()
    test_exclude_wins_on_mixed_label()
    test_japanese_labels()
    test_press_local_is_excluded()
    test_role_from_label()
    test_label_near_email()
    test_rank_ordering_unchanged()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
