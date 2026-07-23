"""URL 予算枯渇でタイトル照合フォールバックが餓死しないこと（第1段・最小修正）。

背景（実測 p96 놀로 / 2026-07-23 ライブ）:
  検索は knollo.store / knollo.co.kr を上位で返し、main crawl は両方を 200 で
  **取得できていた**。にもかかわらず公式が採用されなかったのは、タイトル照合
  フォールバックのループ冒頭が

      if _expired() or len(searched) >= _u_cap: break

  と、「その候補が新規 fetch を要するか」を判定する **前** に予算を見ていたため。
  既訪問 root は searched を増やさない＝予算ゼロ消費なのに、予算切れで 1 件も
  評価されず break していた（google.com 系の誤推定が 25 枠中 17 枠を消費）。

本 PR の修正は 2 点のみ:
  ① already_seen 判定を予算チェックより前へ移動し、予算チェックは新規候補だけに適用
  ② crawl_seen の照合で末尾スラッシュの有無を同一視（_crawl_seen_has）

known paths の展開遅延(A1) と title 予算の予約(B) は **含まない**。

外部 API / DNS / ネットワーク非依存（fetch/search を注入）。
実行（backend ディレクトリで）:
    python tests/test_url_budget_protection.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import web_research_service as w  # noqa: E402

_p = _f = 0


def check(name, cond):
    global _p, _f
    if cond:
        _p += 1; print(f"  ok  - {name}")
    else:
        _f += 1; print(f"  FAIL- {name}")


def html_title(t):
    return f"<html><head><title>{t}</title></head><body>x</body></html>"


SRC = "https://www.wadiz.kr/web/campaign/detail/1"


def run(*, maker, results, roots, max_urls, source_html=None):
    """注入 fetch/search で web_research を実行し、(結果, fetch した URL 列) を返す。"""
    fetched: list[str] = []

    def fetch(u):
        fetched.append(u)
        if u.rstrip("/") == SRC.rstrip("/"):
            return source_html
        for root, h in roots.items():
            if u.rstrip("/") == root.rstrip("/"):
                return h
        return None

    proj = SimpleNamespace(id=96, maker_name=maker, maker_url="", source_url=SRC,
                           source_site="wadiz", title="", product_name="",
                           description="", description_clean="")
    r = w.web_research(proj, None, fetch_fn=fetch,
                       search_fn=lambda _q: list(results),
                       max_urls=max_urls, time_budget=120)
    return r, fetched


def official_of(r):
    return r.get("official_site_url") or ""


# ============ 1. _crawl_seen_has 単体 ============
def test_crawl_seen_has():
    print("test_crawl_seen_has")
    seen = {"https://a.example/", "https://b.example"}
    check("末尾スラッシュ付きが登録済み → スラッシュ無しで True",
          w._crawl_seen_has(seen, "https://a.example"))
    check("末尾スラッシュ付きが登録済み → そのままでも True",
          w._crawl_seen_has(seen, "https://a.example/"))
    check("スラッシュ無しが登録済み → スラッシュ付きで True",
          w._crawl_seen_has(seen, "https://b.example/"))
    check("未登録は False", not w._crawl_seen_has(seen, "https://c.example"))
    check("空集合は False", not w._crawl_seen_has(set(), "https://a.example"))
    check("空文字は False", not w._crawl_seen_has(seen, ""))


# ============ 2. 既訪問 root は予算を消費せず評価される ============
def test_already_seen_costs_no_budget():
    print("test_already_seen_costs_no_budget")
    # main crawl が brandk.co.kr/ を取得して予算を使い切る。タイトル候補 root は
    # brandk.co.kr（末尾スラッシュ無し）で、既訪問なので追加予算は不要。
    results = [
        {"url": "https://brandk.co.kr/", "title": "브랜드케이", "snippet": ""},
        {"url": "https://junksite1.co.kr/x", "title": "무관1", "snippet": ""},
        {"url": "https://junksite2.co.kr/x", "title": "무관2", "snippet": ""},
        {"url": "https://junksite3.co.kr/x", "title": "무관3", "snippet": ""},
    ]
    roots = {"https://brandk.co.kr": html_title("브랜드케이")}
    r, fetched = run(maker="브랜드케이", results=results, roots=roots, max_urls=3)
    searched = r.get("searched_urls") or []
    check("URL 予算は上限どおり消費（超過しない）", len(searched) <= 3)
    check("予算枯渇でも既訪問の本命を採用できる",
          official_of(r) == "https://brandk.co.kr")
    check("既訪問 root は searched に二重計上されない",
          len([u for u in searched if u.rstrip("/") == "https://brandk.co.kr"]) <= 1)


def test_unseen_candidate_still_budget_limited():
    print("test_unseen_candidate_still_budget_limited")
    # タイトル候補が **未訪問** の場合は従来どおり予算に従う（予算を増やさない）。
    # junk で予算を使い切り、未訪問の brandk.co.kr root は評価されない。
    results = [
        {"url": "https://junksite1.co.kr/x", "title": "무관1", "snippet": ""},
        {"url": "https://junksite2.co.kr/x", "title": "무관2", "snippet": ""},
        {"url": "https://brandk.co.kr/deep/page", "title": "브랜드케이", "snippet": ""},
    ]
    roots = {"https://brandk.co.kr": html_title("브랜드케이")}
    r, _ = run(maker="브랜드케이", results=results, roots=roots, max_urls=2)
    searched = r.get("searched_urls") or []
    check("未訪問候補では予算上限を超えない", len(searched) <= 2)
    check("予算切れなら未訪問候補は採用しない（予算の二重化をしない）",
          not official_of(r))


# ============ 3. p96 놀로（ライブ実測データの再現） ============
# 2026-07-23 のライブ実行で Brave が返した page candidate 列（順序そのまま）。
# google.com 系の誤推定で 25 枠が枯渇し、既取得の knollo.* が評価されなかった状況を
# 「予算枯渇 + 本命は既訪問」という本質だけ残して再現する（ネットワーク非依存）。
P96_RESULTS = [
    {"url": "https://linktr.ee/knollo.square", "title": "knollo square Official", "snippet": ""},
    {"url": "https://pf.kakao.com/_QQHub", "title": "카카오톡채널 - 놀로스토어", "snippet": ""},
    {"url": "https://www.knollo.co.kr/", "title": "놀로스퀘어", "snippet": ""},
    {"url": "https://www.knollo.store/", "title": "놀로 knollo", "snippet": ""},
    {"url": "http://www.knollo.co.kr/content/sub/aqua_center.html",
     "title": "어떡하면 반려가족 모두가", "snippet": ""},
    {"url": "https://platum.kr/archives/199247", "title": "스파크펫, 놀로 출시", "snippet": ""},
    {"url": "https://www.newsis.com/view/NISX20221221_0002131124", "title": "뉴시스", "snippet": ""},
    {"url": "https://www.knollo.store/home", "title": "놀로 knollo", "snippet": ""},
    {"url": "https://shoppinglive.naver.com/channels/92191", "title": "놀로스토어 쇼핑라이브", "snippet": ""},
    {"url": "https://m.gsshop.com/section/brandSect/195520", "title": "놀로 - GS SHOP", "snippet": ""},
    {"url": "https://www.oraeorae.com/m/product_list.html", "title": "놀로 상품", "snippet": ""},
    {"url": "https://news.mt.co.kr/mtview.php?no=2021042910595839484", "title": "머니투데이", "snippet": ""},
    {"url": "https://www.knollo.co.kr/sub/terms_2.html", "title": "놀로스퀘어 약관", "snippet": ""},
]
P96_ROOTS = {
    "https://www.knollo.store": html_title("놀로 knollo | 반려동물 간식·용품·케어 전문몰"),
    "https://www.knollo.co.kr": html_title("놀로스퀘어"),
    "https://litt.ly": html_title("리틀리 | 무료로 쉽게 시작하는 나만의 홈페이지"),
}


def test_p96_recovered_at_default_budget():
    print("test_p96_recovered_at_default_budget")
    r, _ = run(maker="놀로", results=P96_RESULTS, roots=P96_ROOTS, max_urls=w.MAX_URLS)
    got = official_of(r)
    check("p96: 既定 MAX_URLS で公式を採用する",
          got in ("https://www.knollo.store", "https://www.knollo.co.kr"))
    check("p96: URL 予算を超過しない", len(r.get("searched_urls") or []) <= w.MAX_URLS)
    check("p96: litt.ly を採用しない", "litt.ly" not in got)


def test_p96_recovered_under_tight_budget():
    print("test_p96_recovered_under_tight_budget")
    # 予算を絞って「枯渇状態」を強制しても、既訪問の knollo.* を評価できる。
    r, _ = run(maker="놀로", results=P96_RESULTS, roots=P96_ROOTS, max_urls=4)
    got = official_of(r)
    check("p96: 予算 4 でも公式を採用（既訪問なので予算ゼロ消費）",
          got in ("https://www.knollo.store", "https://www.knollo.co.kr"))
    check("p96: 予算 4 を超過しない", len(r.get("searched_urls") or []) <= 4)


# ============ 4. reject / 既存成功ケースの維持 ============
def test_p114_reject_maintained():
    print("test_p114_reject_maintained")
    results = [{"url": "https://lgau.co.kr", "title": "주식회사 올음 아이스크림 메이커", "snippet": ""}]
    roots = {"https://lgau.co.kr": html_title("LG전자 B2B 공식커머셜 전문점 올음")}
    r, _ = run(maker="주식회사 올음", results=results, roots=roots, max_urls=w.MAX_URLS)
    check("p114: external_brand_identity_conflict で不採用のまま", not official_of(r))


def test_existing_success_cases_maintained():
    print("test_existing_success_cases_maintained")
    for maker, root, ident in [
        ("퍼시몬", "https://monshop.co.kr", "퍼시몬(Persimmon)"),
        ("경성건강원", "https://gswon.com", "경성건강원"),
        ("골드뷰티", "https://goldbeauty.kr", "주식회사 골드뷰티"),
        ("도서출판 무지개", "https://rainbowbooks.co.kr", "도서출판 무지개"),
        ("나노랩", "https://nanowt.com", "나노랩"),
        ("어라운드엑스", "https://aroundx.kr", "어라운드엑스"),
        ("오피스허브", "https://4582.kr", "오피스허브"),
    ]:
        results = [{"url": root + "/", "title": ident, "snippet": ""}]
        r, _ = run(maker=maker, results=results, roots={root: html_title(ident)},
                   max_urls=w.MAX_URLS)
        check(f"{maker}: {root} を採用（維持）", official_of(r) == root)


def test_reject_cases_maintained():
    print("test_reject_cases_maintained")
    # 小売/求人/別会社/エラーページ/取得不能は引き続き採用しない（FP を増やさない）。
    for label, maker, root, ident in [
        ("小売ssg", "도서출판 무지개", "https://m-shinsegaemall.ssg.com", "신세계몰"),
        ("求人jobkorea", "키움하우스", "https://m.jobkorea.co.kr", "키움하우스 채용"),
        ("別会社", "이대원", "https://hyundaihwarang.com", "현대화랑"),
        ("エラーページ", "에이지크리에이터", "https://gshock.casio.com", "Access Denied"),
    ]:
        results = [{"url": root + "/", "title": f"{maker} 관련", "snippet": ""}]
        r, _ = run(maker=maker, results=results, roots={root: html_title(ident)},
                   max_urls=w.MAX_URLS)
        check(f"{label}: 採用しない", not official_of(r))
    # 取得不能（root が None）
    r, _ = run(maker="리뉴식스",
               results=[{"url": "https://renew6.com/", "title": "리뉴식스", "snippet": ""}],
               roots={}, max_urls=w.MAX_URLS)
    check("取得不能: 採用しない", not official_of(r))


# ============ 5. ラテン/日本語/中国語 maker の回帰なし ============
def test_non_korean_makers_unaffected():
    print("test_non_korean_makers_unaffected")
    # page 由来の公式（ラテン名）は従来どおり採用され、予算修正の影響を受けない。
    src = '<html><body><a href="https://gearsmith.com">Official Website</a></body></html>'
    r, _ = run(maker="Gearsmith", source_html=src,
               results=[{"url": "https://gearsmith.com/", "title": "Gearsmith", "snippet": ""}],
               roots={"https://gearsmith.com": html_title("Gearsmith")},
               max_urls=w.MAX_URLS)
    check("ラテン maker: page 由来公式を採用（維持）",
          official_of(r).startswith("https://gearsmith.com"))
    # 日本語 maker のタイトル照合も従来どおり動く
    r2, _ = run(maker="株式会社ニホンブランド",
                results=[{"url": "https://nihonbrand.co.jp/", "title": "株式会社ニホンブランド", "snippet": ""}],
                roots={"https://nihonbrand.co.jp": html_title("株式会社ニホンブランド")},
                max_urls=w.MAX_URLS)
    check("日本語 maker: タイトル照合で採用（維持）",
          official_of(r2) == "https://nihonbrand.co.jp")
    # 中国語 maker
    r3, _ = run(maker="裡外生活",
                results=[{"url": "https://leewayworld.com/", "title": "裡外生活", "snippet": ""}],
                roots={"https://leewayworld.com": html_title("裡外生活")},
                max_urls=w.MAX_URLS)
    check("中国語 maker: タイトル照合で採用（維持）",
          official_of(r3) == "https://leewayworld.com")
    # 別会社（不一致）は言語を問わず採用しない
    r4, _ = run(maker="Gearsmith",
                results=[{"url": "https://otherco.com/", "title": "Gearsmith review", "snippet": ""}],
                roots={"https://otherco.com": html_title("Other Company")},
                max_urls=w.MAX_URLS)
    check("identity 不一致は採用しない（言語非依存）", not official_of(r4))


def main():
    test_crawl_seen_has()
    test_already_seen_costs_no_budget()
    test_unseen_candidate_still_budget_limited()
    test_p96_recovered_at_default_budget()
    test_p96_recovered_under_tight_budget()
    test_p114_reject_maintained()
    test_existing_success_cases_maintained()
    test_reject_cases_maintained()
    test_non_korean_makers_unaffected()
    print(f"\n{_p} passed / {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
