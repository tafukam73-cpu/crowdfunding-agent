"""公式サイト確定ガードの fixture テスト（実案件で観測した誤判定を固定する）。

前フェーズの実 DB で、公式サイトとして誤保存されていた実 URL：
  lg.com/tw/projectors/...（無関係大企業）, reurl.cc（短縮URL）, m.me（Messenger）,
  hanboost.kickbooster.me（販促ツール）, shopping.parenting.com.tw（ECモール/メディア）。
これらを拒否し、正当なメーカードメインは受理することを固定する。

実行: docker exec cfagent-backend python tests/test_official_site_guard.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(tempfile.gettempdir(), 'osguard.sqlite')}"
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import contact_discovery_service as cds  # noqa: E402

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


def test_reject_known_false_positives():
    print("test_reject_known_false_positives")
    reject = [
        ("短縮URL reurl.cc", "https://reurl.cc/abcd"),
        ("Messenger m.me", "http://m.me/somepage"),
        ("販促 kickbooster", "https://hanboost.kickbooster.me"),
        ("リンク集約 linktr.ee", "https://linktr.ee/somemaker"),
        ("ECモール amazon", "https://www.amazon.com/dp/B0XXXX"),
        ("ECメディア shopping.parenting", "https://shopping.parenting.com.tw/item/1"),
        ("SNS単体 instagram", "https://www.instagram.com/somemaker"),
        ("短縮 bit.ly", "https://bit.ly/xyz"),
        ("プラットフォーム kickstarter", "https://www.kickstarter.com/projects/x/y"),
    ]
    for name, url in reject:
        check(f"official_site_or_none 拒否: {name}",
              cds.official_site_or_none(url) is None)


def test_accept_valid_maker_domains():
    print("test_accept_valid_maker_domains")
    for name, url in [("sharge.com", "https://sharge.com"),
                      ("sunpack", "https://www.sunpack-packaging.com"),
                      ("own-brand", "https://own-brand.co.kr"),
                      # ラベル境界判定：短縮ヒントの部分文字列で誤除外しない
                      ("form.media (not m.me)", "https://form.media"),
                      ("content.co (not t.co)", "https://content.co"),
                      ("ohm.media", "https://ohm.media")]:
        check(f"official_site_or_none 受理: {name}",
              cds.official_site_or_none(url) == url)


def test_confirm_requires_evidence():
    print("test_confirm_requires_evidence")
    # 無関係大企業ドメイン（lg.com）を Vitesy 案件の公式サイトにしない → uncertain 以下
    r = cds.confirm_official_site(
        "https://www.lg.com/tw/projectors/cinebeam/pf600u/",
        maker_name="Vitesy", product_title="Vitesy Fruit Bowl")
    check("lg.com は accepted にしない", r["decision"] != "accepted")
    check("lg.com に relevance 証拠なし",
          "no_relevance_evidence" in r["rejection_reasons"])

    # 短縮URLは rejected（理由付き）
    r2 = cds.confirm_official_site("https://reurl.cc/x", maker_name="Foo")
    check("reurl.cc は rejected", r2["decision"] == "rejected")
    check("拒否理由が付く", bool(r2["rejection_reasons"]))

    # ドメイン一致＋直リンクの 2 証拠 → accepted
    r3 = cds.confirm_official_site(
        "https://sharge.com", maker_name="Sharge", direct_linked=True)
    check("ドメイン一致＋直リンクで accepted", r3["decision"] == "accepted")
    check("証拠が 2 つ以上", len(r3["evidence"]) >= 2)

    # 証拠 1 つ（ドメイン一致のみ）→ uncertain（確定はしない）
    r4 = cds.confirm_official_site("https://sharge.com", maker_name="Sharge")
    check("証拠1つは uncertain", r4["decision"] == "uncertain")


def main():
    test_reject_known_false_positives()
    test_accept_valid_maker_domains()
    test_confirm_requires_evidence()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
