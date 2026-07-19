"""web_research の maker 所有関係検証（誤検出防止）と per-case 時間予算のオフライン検証。

実在 API に依存しない mock/fixture のみ。E03（Search-Driven 実測）で maker_owned へ
混入した第三者連絡先（team@backerviews.com / the-ethos.co / tuffselectph@gmail.com）が
除外されることを回帰 fixture として検証する。

実行（backend ディレクトリで）:
    python tests/test_web_research_ownership.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import web_research_service as w  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


# ---------------- 1〜9: 所有関係ヘルパーの単体検証（source_ownership 再利用） ----------------
def test_email_ownership_rules() -> None:
    print("test_email_ownership_rules")
    OFF = "brandco.com"

    # 1) official domain 上のメールは採用
    owned, reason = w._email_maker_ownership("sales@brandco.com", ["https://brandco.com/contact"], OFF)
    check("1. official domain のメールを採用", owned and reason == "official_domain")

    # 3) official subdomain（同一登録ドメイン）は採用
    owned, _ = w._email_maker_ownership("hi@shop.brandco.com", ["https://shop.brandco.com"], OFF)
    check("3. official subdomain のメールを採用", owned)

    # 4) 第三者レビューサイトのメールは除外（registrable ≠ official, unknown）
    owned, reason = w._email_maker_ownership(
        "team@backerviews.com", ["https://backerviews.com/reviews/x"], OFF)
    check("4. review サイトのメールを除外", (not owned) and reason.startswith("third_party"))

    # 5) 無関係な Gmail（公式ページ以外が出典）は除外
    owned, reason = w._email_maker_ownership(
        "random@gmail.com", ["https://someblog.example.com/post"], OFF)
    check("5. 無関係な Gmail を除外", (not owned) and reason == "personal_off_official_page")

    # 6) 公式 Contact ページに明記された Gmail は採用（出典が公式ドメイン）
    owned, reason = w._email_maker_ownership(
        "brandco.official@gmail.com", ["https://brandco.com/contact"], OFF)
    check("6. 公式ページ掲載の Gmail を採用", owned and reason == "personal_on_official_page")

    # 7) redirect 先の official domain（apex/www 正規化）を正しく扱う
    owned_www, _ = w._email_maker_ownership("hi@brandco.com", ["https://www.brandco.com/"], "www.brandco.com")
    owned_apex, _ = w._email_maker_ownership("hi@www.brandco.com", ["https://brandco.com/"], "brandco.com")
    check("7. redirect 先 official domain を registrable で一致判定", owned_www and owned_apex)

    # 8) backerviews.com のような第三者メールを maker-owned にしない（出典が公式でも registrable 不一致）
    owned, _ = w._email_maker_ownership("team@backerviews.com", ["https://brandco.com/press"], OFF)
    check("8. 第三者ドメインは公式ページ掲載でも maker-owned にしない", not owned)

    # 9) official 候補未確定（official_domain 無し）のとき unknown メールを採用しない
    owned, reason = w._email_maker_ownership("info@unknown-domain.com", ["https://x"], "")
    check("9. official 未確定なら unknown メールを不採用", (not owned) and reason == "no_verified_official")
    owned_p, _ = w._email_maker_ownership("who@gmail.com", ["https://x"], "")
    check("9b. official 未確定なら Gmail も不採用", not owned_p)


def test_form_ownership_rules() -> None:
    print("test_form_ownership_rules")
    OFF = "brandco.com"
    # 2) official domain 上の form は採用
    check("2. official domain の form を採用",
          w._form_maker_owned("https://brandco.com/contact", OFF))
    check("2b. official subdomain の form を採用",
          w._form_maker_owned("https://shop.brandco.com/pages/contact", OFF))
    # 第三者 form は除外
    check("第三者ドメインの form を除外",
          not w._form_maker_owned("https://the-ethos.co/contact", OFF))
    check("official 未確定なら form も不採用",
          not w._form_maker_owned("https://brandco.com/contact", ""))


# ---------------- 10: 既存の正常な contact extraction を壊さない（end-to-end） ----------------
class _BrandCoProject:
    id = 1
    title = "Cool Lamp"
    maker_name = "BrandCo"
    maker_url = "https://brandco.com"
    source_url = "https://www.kickstarter.com/projects/brandco/cool-lamp"
    source_site = "kickstarter"


_CONTACT_HTML = """
<html><body>
  <p>Sales: <a href="mailto:sales@brandco.com">sales@brandco.com</a></p>
  <p>partnership@brandco.com</p>
  <a href="mailto:support@kickstarter.com">platform help</a>
</body></html>
"""
_ROOT_HTML = """
<html><body>
  <a href="/contact">Contact us</a>
  <p>Hello! info@brandco.com</p>
</body></html>
"""
_PAGES = {"https://brandco.com": _ROOT_HTML, "https://brandco.com/contact": _CONTACT_HTML}


def test_existing_extraction_intact() -> None:
    print("test_existing_extraction_intact")
    res = w.web_research(
        _BrandCoProject(), None,
        fetch_fn=lambda u: _PAGES.get(u), search_fn=lambda q: ["https://brandco.com/contact"],
    )
    emails = {e["email"].lower() for e in res["discovered_emails"]}
    check("10. sales@brandco.com を維持", "sales@brandco.com" in emails)
    check("10. partnership@brandco.com を維持", "partnership@brandco.com" in emails)
    check("10. info@brandco.com を維持", "info@brandco.com" in emails)
    check("10. platform メールは除外", "support@kickstarter.com" not in emails)
    check("10. 全 maker メールに maker_owned フラグ",
          all(e.get("maker_owned") for e in res["discovered_emails"]))
    check("10. official form を保持", any("/contact" in f for f in res["discovered_forms"]))


# ---------------- E03 回帰: 第三者連絡先が maker_owned に混入しない ----------------
class _E03Project:
    """E03 相当（メーカー公式は tuffselect-like、第三者レビュー/紹介サイトが混在）。"""
    id = 3
    title = "TUFF Select EDC Wallet"
    maker_name = "TUFF Select"
    maker_url = None  # 公式未登録 → 検索/クロールで推定
    source_url = "https://www.kickstarter.com/projects/tuffselect/edc-wallet"
    source_site = "kickstarter"


# クラファンページ＝公式サイトリンク＋第三者リンクが混在。official は tuffselect.com。
_E03_CF = (
    '<html><body>'
    '<a href="https://tuffselect.com">Official Site</a>'
    '<a href="https://backerviews.com/projects/tuff-select">Reviews</a>'
    '<a href="https://the-ethos.co/blog/tuff-select">Ethos feature</a>'
    '</body></html>'
)
_E03_OFFICIAL = (
    '<html><body><a href="/contact">Contact</a>'
    '<p>Wholesale: <a href="mailto:sales@tuffselect.com">sales@tuffselect.com</a></p>'
    '</body></html>'
)
_E03_OFFICIAL_CONTACT = (
    '<html><body><a href="mailto:hello@tuffselect.com">hello</a></body></html>'
)
# 第三者ページ（クロールされても maker_owned に入ってはならない）。
_E03_BACKERVIEWS = (
    '<html><body>Contact us: <a href="mailto:team@backerviews.com">team@backerviews.com</a>'
    '<a href="/contact">contact</a></body></html>'
)
_E03_ETHOS = (
    '<html><body><a href="mailto:tuffselectph@gmail.com">seller</a>'
    '<a href="/contact">contact</a></body></html>'
)
_E03_PAGES = {
    "https://www.kickstarter.com/projects/tuffselect/edc-wallet": _E03_CF,
    "https://tuffselect.com": _E03_OFFICIAL,
    "https://tuffselect.com/contact": _E03_OFFICIAL_CONTACT,
    "https://backerviews.com/projects/tuff-select": _E03_BACKERVIEWS,
    "https://backerviews.com/contact": _E03_BACKERVIEWS,
    "https://the-ethos.co/blog/tuff-select": _E03_ETHOS,
    "https://the-ethos.co/contact": _E03_ETHOS,
}


def _e03_fetch(url: str):
    return _E03_PAGES.get(url.rstrip("/")) or _E03_PAGES.get(url)


def _e03_search(query: str):
    # 検索結果に公式と第三者が混在（Brave 相当）。
    return [
        {"url": "https://tuffselect.com/", "title": "TUFF Select Official", "snippet": "EDC"},
        {"url": "https://backerviews.com/projects/tuff-select", "title": "Reviews", "snippet": ""},
        {"url": "https://the-ethos.co/blog/tuff-select", "title": "Ethos", "snippet": ""},
    ]


def test_e03_regression_third_party_excluded() -> None:
    print("test_e03_regression_third_party_excluded")
    res = w.web_research(_E03Project(), None, fetch_fn=_e03_fetch, search_fn=_e03_search)
    maker = {e["email"].lower() for e in res["discovered_emails"]}
    tp = {e["email"].lower() for e in res["third_party_emails"]}

    check("E03. 公式ドメイン tuffselect.com を推定", (res["official_site_url"] or "").find("tuffselect.com") >= 0)
    check("E03. sales@tuffselect.com を maker-owned に採用", "sales@tuffselect.com" in maker)
    check("E03. team@backerviews.com は maker-owned から除外", "team@backerviews.com" not in maker)
    check("E03. the-ethos.co 由来 gmail は maker-owned から除外", "tuffselectph@gmail.com" not in maker)
    check("E03. tuffselectph@gmail.com（第三者ページの Gmail）は不採用", "tuffselectph@gmail.com" not in maker)
    check("E03. 第三者メールは third_party_emails に分離保持",
          ("team@backerviews.com" in tp) or ("tuffselectph@gmail.com" in tp))
    forms = res["discovered_forms"]
    check("E03. maker form は公式ドメインのみ",
          all("tuffselect.com" in f for f in forms))
    check("E03. 第三者 contact は maker form に入らない",
          not any(("backerviews.com" in f or "the-ethos.co" in f) for f in forms))


# ---------------- per-case 時間予算 / early_exit ----------------
def test_time_budget_and_early_exit() -> None:
    print("test_time_budget_and_early_exit")

    # max_queries を明示的に絞ると実行クエリ数が制限される。
    res = w.web_research(
        _BrandCoProject(), None,
        fetch_fn=lambda u: _PAGES.get(u), search_fn=lambda q: ["https://brandco.com/contact"],
        max_queries=3,
    )
    check("max_queries で実行クエリ数を制限", len(res["searched_queries"]) <= 3)

    # early_exit: 公式メールを得たら残り候補を巡回しない（クロール数が全件未満）。
    many = {f"https://blog{i}.example.com/x": "<html>noise</html>" for i in range(20)}
    pages = {**_PAGES, **many}

    def search_many(q):
        return ["https://brandco.com/contact"] + list(many.keys())

    res2 = w.web_research(
        _BrandCoProject(), None,
        fetch_fn=lambda u: pages.get(u), search_fn=search_many, early_exit=True,
    )
    check("early_exit で公式連絡先取得後に打ち切り",
          res2.get("stop_reason") == "early_exit_sufficient")
    check("early_exit 時も maker メールを最低1件取得",
          any(e["email"].endswith("@brandco.com") for e in res2["discovered_emails"]))
    check("early_exit でノイズ候補を全巡回しない",
          res2["debug_counts"]["crawled"] < 20)

    # time_budget=0 相当（極小）で timeout 理由が付与され、部分結果を失わない。
    res3 = w.web_research(
        _BrandCoProject(), None,
        fetch_fn=lambda u: _PAGES.get(u), search_fn=lambda q: ["https://brandco.com/contact"],
        time_budget=0.0001,
    )
    check("time_budget 超過で stop_reason=timeout", res3.get("stop_reason") == "timeout")
    check("timeout でも結果 dict を返す（部分結果保持）", "discovered_emails" in res3)


def main() -> int:
    test_email_ownership_rules()
    test_form_ownership_rules()
    test_existing_extraction_intact()
    test_e03_regression_third_party_excluded()
    test_time_budget_and_early_exit()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
