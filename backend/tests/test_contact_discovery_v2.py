"""Contact Discovery v2 のオフライン検証（ネットワーク/外部 API なし・pytest 非依存）。

fetch_fn（ページ取得。404/非200 は None）と search_fn（検索）を注入し、
人間の検索手順に近いフロー（公式サイト候補探索 → 優先クロール(Contact/About/...)
→ header/footer/meta/schema.org 解析 → LinkedIn → メール抽出 → 検証 → 信頼度★）を
検証する。最後に「実案件 5 件相当」でメール/公式サイト/LinkedIn の取得率を報告する。

実行（backend ディレクトリで）:
    python tests/test_contact_discovery_v2.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# SessionLocal 束縛前に file sqlite を指定（別セッション共有のため）
_DBFILE = os.path.join(tempfile.gettempdir(), "contact_discovery_v2_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import contact_discovery_v2_service as v2  # noqa: E402

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


class FakeProject:
    def __init__(self, **kw):
        self.id = kw.get("id", 1)
        self.title = kw.get("title", "")
        self.maker_name = kw.get("maker_name", "")
        self.maker_url = kw.get("maker_url")
        self.source_url = kw.get("source_url")
        self.source_site = kw.get("source_site", "kickstarter")


def make_fetch(pages: dict):
    """pages に無い URL は None（=404/取得失敗）を返す取得関数。"""
    def fetch(url: str):
        return pages.get(url) or pages.get(url.rstrip("/"))
    return fetch


class FakeSearch:
    """query -> [{url,title}] を返す注入用検索。provider 属性も持つ。"""

    def __init__(self, mapping: dict, provider: str = "bing"):
        self.mapping = mapping
        self.provider = provider

    def __call__(self, query: str):
        return self.mapping.get(query, [])


# ============================================================
# ケース1: 公式サイト（Contact/Footer/About/Privacy）＋ schema.org
# ============================================================
_C1_ROOT = """
<html><head>
  <script type="application/ld+json">
  {"@type":"Organization","name":"LumaCo","email":"hello@lumaco.com",
   "sameAs":["https://www.instagram.com/lumaco","https://www.linkedin.com/company/lumaco"]}
  </script>
</head><body>
  <a href="/contact">Contact</a>
  <a href="/about">About</a>
  <a href="/privacy">Privacy</a>
  <footer>General: <a href="mailto:info@lumaco.com">info@lumaco.com</a></footer>
</body></html>
"""
_C1_CONTACT = """
<html><body>
  <h1>Contact us</h1>
  <p>Sales: <a href="mailto:sales@lumaco.com">sales@lumaco.com</a></p>
  <a href="mailto:no-reply@lumaco.com">noreply</a>
  <a href="mailto:support@kickstarter.com">platform</a>
</body></html>
"""
_C1_ABOUT = "<html><body>About. Reach the founder at founder@lumaco.com</body></html>"
_C1_PRIVACY = "<html><body>Privacy. Data requests: privacy@lumaco.com</body></html>"

_CASE1 = FakeProject(
    id=1, title="Luma Lamp", maker_name="LumaCo",
    maker_url="https://lumaco.com",
    source_url="https://www.kickstarter.com/projects/lumaco/luma-lamp",
)
_C1_PAGES = {
    "https://lumaco.com": _C1_ROOT,
    "https://lumaco.com/contact": _C1_CONTACT,
    "https://lumaco.com/about": _C1_ABOUT,
    "https://lumaco.com/privacy": _C1_PRIVACY,
}


def test_case1_official_tiers_and_schema():
    print("test_case1_official_tiers_and_schema")
    res = v2.discover_v2(
        _CASE1, fetch_fn=make_fetch(_C1_PAGES),
        search_fn=FakeSearch({}),
    )
    check("公式サイトを project_website から確定",
          res["official_site_url"] == "https://lumaco.com"
          and res["official_site_source"] == "project_website")
    by = {e["email"]: e for e in (res["emails"] or [])}
    check("sales@（Contact ページ）は★5", by.get("sales@lumaco.com", {}).get("stars") == 5)
    check("sales@ の信頼度ソースが official_site_contact",
          by.get("sales@lumaco.com", {}).get("confidence_source") == "official_site_contact")
    check("info@（footer）は★4", by.get("info@lumaco.com", {}).get("stars") == 4)
    check("info@ の信頼度ソースが official_site_footer",
          by.get("info@lumaco.com", {}).get("confidence_source") == "official_site_footer")
    check("founder@（About）は★4",
          by.get("founder@lumaco.com", {}).get("stars") == 4
          and by["founder@lumaco.com"]["confidence_source"] == "company_about")
    check("privacy@（Privacy）は★3",
          by.get("privacy@lumaco.com", {}).get("stars") == 3
          and by["privacy@lumaco.com"]["confidence_source"] == "official_site_legal")
    check("schema.org の hello@ は★5（Contact 相当）",
          by.get("hello@lumaco.com", {}).get("stars") == 5)
    check("no-reply は排除", "no-reply@lumaco.com" not in by)
    check("プラットフォームメールは排除", "support@kickstarter.com" not in by)
    check("各メールに取得元URL", all(e.get("source_url") for e in res["emails"]))
    check("最有力メールは★5", res["primary_stars"] == 5)
    check("信頼度スコア=95", res["confidence_score"] == 95)
    check("schema.org から LinkedIn(company) を取得",
          res["linkedin_company_url"] == "https://www.linkedin.com/company/lumaco")
    check("schema.org から Instagram を取得",
          (res["socials"] or {}).get("instagram") == "https://www.instagram.com/lumaco")
    check("Contact ページをフォーム候補化",
          any("/contact" in f for f in (res["forms"] or [])))
    # 進捗ステップ（どこを探索しているか）
    phases = [s["phase"] for s in res["steps"]]
    check("進捗ステップに collect/official_site/crawl/linkedin/extract",
          all(p in phases for p in
              ["collect", "official_site", "crawl", "linkedin", "extract"]))


# ============================================================
# ケース2: 公式サイトを検索で発見（project.website 無し）
# ============================================================
_C2_ROOT = """
<html><body>
  <a href="/contact-us">Contact</a>
  <footer><a href="mailto:contact@novapods.com">contact@novapods.com</a></footer>
</body></html>
"""
_C2_CONTACT = "<html><body>Partnerships: partners@novapods.com</body></html>"
_CASE2 = FakeProject(
    id=2, title="Nova Pods", maker_name="NovaPods", maker_url=None,
    source_url="https://www.kickstarter.com/projects/nova/nova-pods",
)
_C2_PAGES = {
    "https://novapods.com": _C2_ROOT,
    "https://novapods.com/contact-us": _C2_CONTACT,
}
_C2_SEARCH = FakeSearch({
    "NovaPods official site": [
        {"url": "https://www.kickstarter.com/projects/nova", "title": "KS"},
        {"url": "https://novapods.com", "title": "NovaPods Official"},
    ],
    "NovaPods linkedin": [
        {"url": "https://www.linkedin.com/company/novapods", "title": "NovaPods"},
    ],
})


def test_case2_official_from_search():
    print("test_case2_official_from_search")
    res = v2.discover_v2(_CASE2, fetch_fn=make_fetch(_C2_PAGES), search_fn=_C2_SEARCH)
    check("検索で公式サイトを確定",
          res["official_site_url"] == "https://novapods.com"
          and res["official_site_source"] == "search")
    check("プラットフォーム候補は採用しない",
          all("kickstarter" not in c["url"] for c in res["official_site_candidates"]))
    by = {e["email"]: e for e in (res["emails"] or [])}
    check("partners@ を Contact から取得", "partners@novapods.com" in by)
    check("contact@ を footer から取得（★4）",
          by.get("contact@novapods.com", {}).get("stars") == 4)
    check("LinkedIn(company) を検索から取得",
          res["linkedin_company_url"] == "https://www.linkedin.com/company/novapods")
    check("検索プロバイダーを記録", res["search_provider"] == "bing")


# ============================================================
# ケース3: 公式サイト未発見 → クラファンページからメール補完（★2）
# ============================================================
_C3_CAMPAIGN = """
<html><body>
  <p>Questions? Email us at team@gadgetworks.io</p>
</body></html>
"""
_CASE3 = FakeProject(
    id=3, title="Gadget X", maker_name="GadgetWorks", maker_url=None,
    source_url="https://www.indiegogo.com/projects/gadget-x",
    source_site="indiegogo",
)
_C3_PAGES = {"https://www.indiegogo.com/projects/gadget-x": _C3_CAMPAIGN}


def test_case3_crowdfunding_fallback():
    print("test_case3_crowdfunding_fallback")
    res = v2.discover_v2(_CASE3, fetch_fn=make_fetch(_C3_PAGES), search_fn=FakeSearch({}))
    check("公式サイト未発見", res["official_site_url"] is None)
    by = {e["email"]: e for e in (res["emails"] or [])}
    check("クラファンページから team@ を取得", "team@gadgetworks.io" in by)
    check("クラファン由来は★2",
          by.get("team@gadgetworks.io", {}).get("stars") == 2
          and by["team@gadgetworks.io"]["confidence_source"] == "crowdfunding_page")
    check("信頼度スコア=40", res["confidence_score"] == 40)


# ============================================================
# ケース4: 404 ページは解析しない・ダミー/example/test を排除
# ============================================================
_C4_ROOT = """
<html><body>
  <a href="/contact">Contact</a>
  <a href="/about">About</a>
  <footer><a href="mailto:hi@brightgear.com">hi@brightgear.com</a></footer>
</body></html>
"""
# /contact は「404（=None）」を返す想定でページ辞書に含めない。
_C4_ABOUT = """
<html><body>
  About. dummy@example.com test@test.com placeholder@dummy.io
  Real contact: sales@brightgear.com
</body></html>
"""
_CASE4 = FakeProject(
    id=4, title="Bright Gear", maker_name="BrightGear",
    maker_url="https://brightgear.com",
    source_url="https://www.kickstarter.com/projects/bg/bright-gear",
)
_C4_PAGES = {
    "https://brightgear.com": _C4_ROOT,
    "https://brightgear.com/about": _C4_ABOUT,
    # /contact は存在しない（404）
}


def test_case4_404_and_dummy_filtering():
    print("test_case4_404_and_dummy_filtering")
    res = v2.discover_v2(_CASE4, fetch_fn=make_fetch(_C4_PAGES), search_fn=FakeSearch({}))
    crawled = {c["url"]: c for c in (res["crawled_pages"] or [])}
    check("/contact は取得失敗として記録",
          crawled.get("https://brightgear.com/contact", {}).get("ok") is False)
    check("/about は取得成功",
          crawled.get("https://brightgear.com/about", {}).get("ok") is True)
    by = {e["email"]: e for e in (res["emails"] or [])}
    check("example/dummy/test を排除",
          not any(d in by for d in
                  ["dummy@example.com", "test@test.com", "placeholder@dummy.io"]))
    check("実在の sales@ は取得（About=★4）",
          by.get("sales@brightgear.com", {}).get("stars") == 4)
    check("footer の hi@ は★4", by.get("hi@brightgear.com", {}).get("stars") == 4)


# ============================================================
# ケース5: DB 保存（run_contact_discovery_v2）と sales_contacts 反映
# ============================================================
def test_case5_db_persist():
    print("test_case5_db_persist")
    from app.db.base import Base
    from app.db.session import SessionLocal, engine
    from app.models.project import Project

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        proj = Project(
            title="Aero Bottle", source_site="kickstarter",
            source_url="https://www.kickstarter.com/projects/aero/aero-bottle",
            maker_name="AeroBottle", maker_url="https://aerobottle.com",
        )
        db.add(proj)
        db.commit()
        db.refresh(proj)

        pages = {
            "https://aerobottle.com": (
                "<html><body><a href='/contact'>Contact</a>"
                "<footer><a href='mailto:info@aerobottle.com'>info</a></footer>"
                "</body></html>"
            ),
            "https://aerobottle.com/contact": (
                "<html><body>Sales: sales@aerobottle.com</body></html>"
            ),
        }
        search = FakeSearch({
            "AeroBottle linkedin": [
                {"url": "https://www.linkedin.com/company/aerobottle"}],
        })
        saved = v2.run_contact_discovery_v2(
            db, proj, fetch_fn=make_fetch(pages), search_fn=search,
        )
        check("v2_researched 保存", saved.v2_researched is True)
        check("v2_status=completed", saved.v2_status == "completed")
        check("v2_official_site_url 保存",
              saved.v2_official_site_url == "https://aerobottle.com")
        check("v2_emails 保存", bool(saved.v2_emails))
        check("v2_primary_email=sales@（★5）",
              saved.v2_primary_email == "sales@aerobottle.com"
              and saved.v2_primary_stars == 5)
        check("v2_linkedin_company_url 保存",
              saved.v2_linkedin_company_url == "https://www.linkedin.com/company/aerobottle")
        check("v2_steps 保存", bool(saved.v2_steps))
        check("v2_summary 保存", bool(saved.v2_summary))
    finally:
        db.close()


# ============================================================
# 取得率レポート（実案件 5 件相当）
# ============================================================
def report_acquisition_rates():
    print("\n=== 取得率レポート（実案件 5 件相当・オフライン再現）===")
    cases = [
        ("Case1 LumaCo", _CASE1, make_fetch(_C1_PAGES), FakeSearch({})),
        ("Case2 NovaPods", _CASE2, make_fetch(_C2_PAGES), _C2_SEARCH),
        ("Case3 GadgetWorks", _CASE3, make_fetch(_C3_PAGES), FakeSearch({})),
        ("Case4 BrightGear", _CASE4, make_fetch(_C4_PAGES), FakeSearch({})),
        (
            "Case5 AeroBottle",
            FakeProject(id=5, title="Aero Bottle", maker_name="AeroBottle",
                        maker_url="https://aerobottle.com",
                        source_url="https://www.kickstarter.com/projects/aero/aero-bottle"),
            make_fetch({
                "https://aerobottle.com": (
                    "<html><body><a href='/contact'>Contact</a>"
                    "<footer><a href='mailto:info@aerobottle.com'>info</a></footer>"
                    "</body></html>"),
                "https://aerobottle.com/contact":
                    "<html><body>Sales: sales@aerobottle.com</body></html>",
            }),
            FakeSearch({"AeroBottle linkedin": [
                {"url": "https://www.linkedin.com/company/aerobottle"}]}),
        ),
    ]
    n = len(cases)
    email_hits = site_hits = li_hits = 0
    for name, proj, fetch, search in cases:
        res = v2.discover_v2(proj, fetch_fn=fetch, search_fn=search)
        got_email = bool(res["emails"])
        got_site = bool(res["official_site_url"])
        got_li = bool(res["linkedin_company_url"] or res["linkedin_person_url"])
        email_hits += got_email
        site_hits += got_site
        li_hits += got_li
        top = res["emails"][0] if res["emails"] else None
        print(f"  - {name}: メール={'○' if got_email else '×'}"
              f"{f' ({top['email']} ★{top['stars']})' if top else ''}"
              f" / 公式サイト={'○' if got_site else '×'}"
              f" / LinkedIn={'○' if got_li else '×'}")
    print(f"\n  メール取得率      : {email_hits}/{n} = {100*email_hits//n}%")
    print(f"  公式サイト取得率  : {site_hits}/{n} = {100*site_hits//n}%")
    print(f"  LinkedIn取得率    : {li_hits}/{n} = {100*li_hits//n}%")
    # 取得率が想定どおり（メール 4/5・公式 4/5・LinkedIn 2/5）
    check("メール取得率 >= 80%", email_hits >= 4)
    check("公式サイト取得率 >= 80%", site_hits >= 4)
    check("LinkedIn取得率 >= 40%", li_hits >= 2)


def main() -> int:
    test_case1_official_tiers_and_schema()
    test_case2_official_from_search()
    test_case3_crowdfunding_fallback()
    test_case4_404_and_dummy_filtering()
    test_case5_db_persist()
    report_acquisition_rates()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
