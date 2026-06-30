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
    print("\n" + ("すべて期待どおり（巡回1件問題は解消）" if ok else "未達あり"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
