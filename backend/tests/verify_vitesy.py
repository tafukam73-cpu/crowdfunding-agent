"""Vitesy Fruit Bowl 再検証（要件7）。ログ・フロー・集計・カテゴリ別URLを可視化。

公式サイト未登録（maker_url=None）の Vitesy を、検索API（Brave 相当）が公式サイト
・Contact・About・SNS を返すケースで再現し、修正後に
  - 巡回URLが1件で止まらず10件以上になる
  - 公式サイトを検索結果から推定して Contact/About まで巡回する
  - SNS・メールを発見する
ことを INFO ログ付きで確認する。

注意：固定フィクスチャ（ライブ検索ではない）。実APIキー設定時は Brave が同様の
結果を返す前提。実ネット検証は別途、起動中アプリ＋BRAVE_SEARCH_API_KEY で実施。

実行（backend ディレクトリで）:
    python tests/verify_vitesy.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# web_research の INFO ログを標準出力に出す（要件1・2・5）
logging.basicConfig(level=logging.INFO, format="LOG %(name)s: %(message)s")

from app.services import search_providers as sp  # noqa: E402
from app.services import web_research_service as w  # noqa: E402


class Vitesy:
    id = 101
    title = "Vitesy Fruit Bowl"
    maker_name = "Vitesy"
    maker_url = None  # 公式サイト未登録（ここがポイント）
    source_url = "https://www.indiegogo.com/projects/vitesy-fruit-bowl"
    source_site = "indiegogo"
    description = "Vitesy Fruit Bowl keeps your fruit fresh with natural materials."
    description_clean = description


PAGES = {
    "https://vitesy.com": (
        '<html><body>'
        '<a href="/pages/contact">Contact</a>'
        '<a href="/about">About</a>'
        '<a href="/wholesale">Wholesale</a>'
        '<a href="https://www.instagram.com/vitesy/">IG</a>'
        '<a href="https://www.facebook.com/vitesy">FB</a>'
        '</body></html>'
    ),
    "https://vitesy.com/pages/contact": (
        '<html><body><a href="mailto:partnership@vitesy.com">partner</a>'
        '<a href="mailto:support@indiegogo.com">platform</a></body></html>'
    ),
    "https://vitesy.com/about": "<html><body>About Vitesy. hello@vitesy.com</body></html>",
    "https://vitesy.com/wholesale": (
        '<html><body><a href="/media/catalog.pdf">Catalog</a></body></html>'
    ),
}


def fetch(url: str):
    return PAGES.get(url.rstrip("/")) or PAGES.get(url)


def brave_like_search(query: str):
    out = [
        {"url": "https://vitesy.com/", "title": "Vitesy – Official Store", "snippet": "Fruit Bowl"},
        {"url": "https://vitesy.com/pages/contact", "title": "Contact Vitesy", "snippet": "get in touch"},
        {"url": "https://vitesy.com/about", "title": "About Vitesy", "snippet": "our story"},
        {"url": "https://www.instagram.com/vitesy/", "title": "Vitesy (@vitesy) • Instagram", "snippet": "Fruit Bowl"},
        {"url": "https://www.facebook.com/vitesy", "title": "Vitesy | Facebook", "snippet": ""},
        {"url": "https://www.linkedin.com/company/vitesy/", "title": "Vitesy | LinkedIn", "snippet": "company"},
        {"url": "https://vitesy.com/media/catalog.pdf", "title": "Vitesy Catalog", "snippet": "press"},
        # ノイズ（除外されるべき）
        {"url": "https://www.instagram.com/indiegogo/", "title": "Indiegogo", "snippet": "platform"},
        {"url": "https://www.facebook.com/sharer/sharer.php?u=x", "title": "Share", "snippet": ""},
        {"url": "https://www.amazon.com/dp/B0XYZ", "title": "Amazon: Vitesy", "snippet": "buy"},
        {"url": "https://www.youtube.com/watch?v=abc", "title": "Vitesy video", "snippet": ""},
        {"url": "https://news.example.org/vitesy-review", "title": "Review", "snippet": "news"},
    ]
    for i in range(10):
        out.append({"url": f"https://aggregator{i}.example.com/vitesy", "title": "listing", "snippet": ""})
    return out


brave_like_search.provider = "brave"  # type: ignore[attr-defined]


# 実際の Brave Web Search API レスポンス構造（web.results / mixed / videos）を模した
# ペイロード。これを parse_brave_results に通して web_research に渡すことで、
# 「Brave レスポンス → 解析 → 探索」の全経路を検証する（要件8）。
BRAVE_PAYLOAD = {
    "type": "search",
    "mixed": {"main": [{"type": "web", "index": i} for i in range(7)]},
    "web": {
        "results": [
            {"title": "Vitesy – Official", "url": "https://vitesy.com/",
             "description": "Fruit Bowl", "profile": {"url": "https://vitesy.com/"}},
            {"title": "Contact Vitesy", "url": "https://vitesy.com/pages/contact",
             "description": "get in touch"},
            {"title": "About Vitesy", "url": "https://vitesy.com/about",
             "description": "our story"},
            {"title": "Vitesy (@vitesy) Facebook", "url": "https://www.facebook.com/vitesy/",
             "description": "Official", "meta_url": {"hostname": "www.facebook.com"}},
            {"title": "Vitesy Instagram", "url": "https://www.instagram.com/vitesy/",
             "description": "Fruit Bowl"},
            {"title": "Vitesy LinkedIn", "url": "https://www.linkedin.com/company/vitesy/",
             "description": "company"},
            {"title": "Indiegogo", "url": "https://www.instagram.com/indiegogo/",
             "description": "platform"},
            {"title": "Amazon", "url": "https://www.amazon.com/dp/B0XYZ",
             "description": "buy"},
        ]
    },
    "videos": {"results": [{"title": "v", "url": "https://www.youtube.com/watch?v=abc"}]},
}


def brave_parsed_search(query: str):
    """Brave のレスポンス JSON を実パーサに通して返す（解析経路も検証）。"""
    return sp.parse_brave_results(BRAVE_PAYLOAD)


brave_parsed_search.provider = "brave"  # type: ignore[attr-defined]


def _run_campaign_page_path() -> bool:
    """要件8の中核：検索0件でも Indiegogo ページ本文から公式サイト・SNSを取得する。"""
    print("\n=== 検索0件 → クラファンページ本文から発見（要件1〜6・8）===")
    cf_url = Vitesy.source_url
    cf_html = (
        '<html><body>'
        '<h1>Vitesy Fruit Bowl</h1>'
        '<a href="https://vitesy.com">Official Website</a>'
        '<a href="https://www.instagram.com/indiegogo/">Indiegogo</a>'  # 運営→除外
        '<a href="https://www.instagram.com/vitesy/">Instagram</a>'
        '<a href="https://www.facebook.com/vitesy">Facebook</a>'
        '<a href="https://www.linkedin.com/company/vitesy/">LinkedIn</a>'
        '<a href="https://www.youtube.com/@vitesy">YouTube</a>'
        '</body></html>'
    )
    local_pages = dict(PAGES)
    local_pages[cf_url] = cf_html

    def fetch_cf(url: str):
        return local_pages.get(url.rstrip("/")) or local_pages.get(url)

    def no_search(query: str):
        return []

    no_search.provider = "duckduckgo"  # type: ignore[attr-defined]

    res = w.web_research(Vitesy(), None, fetch_fn=fetch_cf, search_fn=no_search)
    soc = res["discovered_socials"]
    dc = res["debug_counts"]
    print(f"  検索結果件数: {dc['results']}（0件） / 巡回URL: {dc['crawled']}")
    print(f"  フロー: {res['research_flow']}")
    print(f"  公式: {res.get('primary_contact_form_url') or ''} 巡回に vitesy.com 含む: "
          f"{any('vitesy.com' in u for u in res['searched_urls'])}")
    for plat in ("facebook", "instagram", "linkedin", "youtube"):
        print(f"  {plat}: {soc.get(plat)}")
    checks = [
        ("検索0件でも公式サイト発見", any("vitesy.com" in u for u in res["searched_urls"])),
        ("Contact まで巡回", any("contact" in u.lower() and "vitesy.com" in u for u in res["searched_urls"])),
        ("Facebook 取得", soc.get("facebook") == "https://www.facebook.com/vitesy"),
        ("Instagram 取得", soc.get("instagram") == "https://www.instagram.com/vitesy/"),
        ("LinkedIn 取得", soc.get("linkedin") == "https://www.linkedin.com/company/vitesy/"),
        ("運営SNS(indiegogo)誤採用なし", "indiegogo" not in (soc.get("instagram") or "")),
        ("メール取得", bool(res["discovered_emails"])),
    ]
    ok = True
    for label, cond in checks:
        print(f"  [{'OK' if cond else 'NG'}] {label}")
        ok = ok and cond
    return ok


def _run_unicode_fallback_path() -> bool:
    """要件：Unicodeタイトルで例外なし＋長い件名が0件なら短縮クエリで発見する。"""
    print("\n=== Unicode正規化 ＋ 短縮クエリ・フォールバック ===")
    ok = True
    # 1. Unicodeタイトルで sanitize → UTF-8 URL が ascii-safe（'ascii' codec 例外なし）
    for t in ["AfriK’Ecotour", "Vitesy Fruit Bowl: Reinventing Fruit Freshness"]:
        q = sp.sanitize_query(t)
        url = sp._utf8_query_url(sp.BRAVE_ENDPOINT, {"q": q, "count": 10})
        try:
            url.encode("ascii")
            safe = True
        except UnicodeEncodeError:
            safe = False
        print(f"  sanitize {t!r} -> {q!r} : URL ascii-safe={safe}")
        ok = ok and safe

    # 2. 長い件名クエリは0件、短縮 "Vitesy ..." で SNS/公式が返る検索をシミュレート。
    #    検索エンジン本体は web_research が build_web_search_queries（多くは引用符付き
    #    の長いクエリ）を投げて 0 件 → フォールバックで短縮クエリを投げる流れを再現。
    fb_results = {
        "Vitesy": [
            {"url": "https://vitesy.com/", "title": "Vitesy Official", "snippet": "Vitesy"},
            {"url": "https://www.facebook.com/vitesy", "title": "Vitesy Facebook", "snippet": "Vitesy"},
            {"url": "https://www.instagram.com/vitesy/", "title": "Vitesy IG", "snippet": "Vitesy"},
            {"url": "https://www.linkedin.com/company/vitesy/", "title": "Vitesy LinkedIn", "snippet": "Vitesy"},
        ],
    }

    def picky_search(query: str):
        # 引用符付き/長い件名クエリは 0 件。短縮クエリだけ結果を返す。
        return fb_results.get(query.strip(), [])

    picky_search.provider = "brave"  # type: ignore[attr-defined]

    # クラファンページHTMLは公式リンクを含めない（フォールバック検索で発見させる）
    def fetch_no_official(url: str):
        if url.rstrip("/") == Vitesy.source_url.rstrip("/"):
            return "<html><body><h1>Vitesy Fruit Bowl</h1></body></html>"
        return fetch(url)

    res = w.web_research(Vitesy(), None, fetch_fn=fetch_no_official, search_fn=picky_search)
    soc = res["discovered_socials"]
    print(f"  フォールバック実行クエリ: {[q for q in res['searched_queries'] if not q.startswith(chr(34))][-5:]}")
    print(f"  巡回URL: {res['debug_counts']['crawled']} / 公式: "
          f"{any('vitesy.com' in u for u in res['searched_urls'])}")
    for plat in ("facebook", "instagram", "linkedin"):
        print(f"  {plat}: {soc.get(plat)}")
    checks = [
        ("短縮フォールバックで公式サイト発見", any("vitesy.com" in u for u in res["searched_urls"])),
        ("Facebook 発見", bool(soc.get("facebook"))),
        ("Instagram 発見", bool(soc.get("instagram"))),
        ("LinkedIn 発見", bool(soc.get("linkedin"))),
    ]
    for label, cond in checks:
        print(f"  [{'OK' if cond else 'NG'}] {label}")
        ok = ok and cond
    return ok


def _run_brave_path() -> bool:
    print("\n=== Brave レスポンス → parse_brave_results → web_research（要件8）===")
    parsed = sp.parse_brave_results(BRAVE_PAYLOAD)
    print(f"  parse_brave_results: {len(parsed)} 件取得")
    for r in parsed[:8]:
        print(f"      {r['url']}")
    res = w.web_research(Vitesy(), None, fetch_fn=fetch, search_fn=brave_parsed_search)
    dc = res["debug_counts"]
    print(f"  検索結果件数: {dc['results']} / 巡回URL: {dc['crawled']} "
          f"(ok {dc['ok']}/fail {dc['failed']})")
    print(f"  フロー: {res['research_flow']}")
    checks = [
        ("Brave解析で結果>=5", len(parsed) >= 5),
        ("facebook.com/vitesy を解析取得",
         any("facebook.com/vitesy" in r["url"] for r in parsed)),
        ("検索結果件数 0→10件以上にできる素地（>=5）", dc["results"] >= 5),
        ("巡回URL 1→10件以上", dc["crawled"] >= 10),
        ("Facebook 発見", bool(res["discovered_socials"].get("facebook"))),
        ("Instagram 発見", bool(res["discovered_socials"].get("instagram"))),
        ("公式サイト発見", any("vitesy.com" in u for u in res["searched_urls"])),
        ("運営SNS(indiegogo)を誤採用しない",
         "indiegogo" not in (res["discovered_socials"].get("instagram") or "")),
    ]
    ok = True
    for label, cond in checks:
        print(f"  [{'OK' if cond else 'NG'}] {label}")
        ok = ok and cond
    return ok


def main() -> int:
    print("=== Vitesy Fruit Bowl 再検証（公式サイト未登録ケース）===\n")
    res = w.web_research(Vitesy(), None, fetch_fn=fetch, search_fn=brave_like_search)

    dc = res["debug_counts"]
    print("\n--- デバッグ集計（要件6）---")
    print(f"  検索クエリ数      : {dc['queries']}")
    print(f"  検索結果件数      : {dc['results']}")
    print(f"  巡回URL数         : {dc['crawled']}")
    print(f"  成功URL数         : {dc['ok']}")
    print(f"  失敗URL数         : {dc['failed']}")
    print(f"  除外URL数         : {dc['excluded']}")
    print(f"  メール抽出対象ページ: {dc['email_pages']}")

    print("\n--- 探索フロー（要件5）---")
    print(f"  {res['research_flow']}")

    print("\n--- 検索結果URL（カテゴリ別・要件3）---")
    cats: dict[str, list[str]] = {}
    for r in res["search_results"]:
        cats.setdefault(r["kind"], []).append(r["url"])
    for kind, urls in cats.items():
        print(f"  [{kind}] {len(urls)}件")
        for u in urls[:6]:
            print(f"      {u}")
    print("  発見SNS:")
    for plat, u in res["discovered_socials"].items():
        print(f"      {plat}: {u}")

    print("\n--- 巡回ページ（成功/失敗・要件1）---")
    for p in res["candidate_pages"]:
        flag = "OK " if p["ok"] else "NG "
        print(f"  {flag} [{p['type']}] {p['url']} (emails={p['emails']})")

    print("\n--- 発見メール ---")
    for e in res["discovered_emails"]:
        print(f"  {e['email']} ({e['tier']} {e['score']}) ← {e['sources']}")

    print("\n--- 期待値チェック（要件7）---")
    checks = [
        ("検索クエリ 10件以上", dc["queries"] >= 10),
        ("検索結果 20件以上", dc["results"] >= 20),
        ("巡回URL 10件以上", dc["crawled"] >= 10),
        ("SNS 1件以上", len(res["discovered_socials"]) >= 1),
        ("公式サイト発見", any("vitesy.com" in u for u in res["searched_urls"])),
        ("Contact 発見", any("contact" in u.lower() for u in res["searched_urls"])),
        ("メール発見", bool(res["discovered_emails"])),
        ("運営メール誤採用なし", all(not e["email"].endswith("@indiegogo.com") for e in res["discovered_emails"])),
    ]
    ok = True
    for label, cond in checks:
        print(f"  [{'OK' if cond else 'NG'}] {label}")
        ok = ok and cond

    ok = _run_campaign_page_path() and ok
    ok = _run_unicode_fallback_path() and ok
    ok = _run_brave_path() and ok

    print("\n" + ("すべて期待どおり（巡回1件問題は解消）" if ok else "未達あり"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
