"""未検証の推定公式に代表パス(WEB_KNOWN_PATHS)を先払い展開しないこと（A1）。

背景（実測 p96 놀로 / 2026-07-23 ライブ）:
  expand_official() は「公式候補の確定」と「代表パス 16 本のキュー投入」を不可分に
  行い、identity / 大企業ブランドガードによる検証は **その後** に走っていた。そのため
  誤推定した候補が撤回されても、先に積んだ 16 本は待ち行列に残り MAX_URLS を食い潰す。
  p96 では play.google.com -> support.google.com -> gemini.google.com の二段誤推定で
  25 枠中 17 枠が google.com に占有され、撤回後の純粋な無駄が 14 枠あった。

A1 の変更:
  - WEB_KNOWN_PATHS の展開を expand_known_paths() へ切り出し、known_paths_expanded
    で同一 root への二重展開を防ぐ
  - inferred=True（未検証の推定候補）では root だけを積み、代表パスは検証通過後に展開
  - maker_url 由来（inferred=False）は従来どおり即時展開
  - root の fetch が失敗した場合だけは保険として展開（recall 保護）

含まないもの: title fallback 専用予算 / MAX_URLS の増加 / ground truth 変更。

外部 API / DNS / ネットワーク非依存（fetch/search を注入）。
実行（backend ディレクトリで）:
    python tests/test_lazy_known_path_expansion.py
"""
from __future__ import annotations

import logging
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


def html_title(t, extra=""):
    return f"<html><head><title>{t}</title>{extra}</head><body>x</body></html>"


SRC = "https://www.wadiz.kr/web/campaign/detail/1"


class LogCap(logging.Handler):
    """web_research のロガーから展開/撤回イベントだけ拾う。"""

    def __init__(self):
        super().__init__()
        self.expanded: list[str] = []      # 実際に代表パスを展開した root
        self.deferred: list[str] = []      # 展開を保留した root
        self.rejected: list[str] = []      # 撤回された候補

    def emit(self, record):
        m = record.getMessage()
        if "known paths expanded" in m:
            self.expanded.append(m)
        elif "known paths DEFERRED" in m or "+16 known paths" in m:
            (self.deferred if "DEFERRED" in m else self.expanded).append(m)
        elif "official REJECTED" in m:
            self.rejected.append(m)


def run(*, maker, results, roots, max_urls=None, source_html=None, maker_url=""):
    """注入 fetch/search で web_research を実行し、(結果, fetch URL 列, ログ) を返す。"""
    fetched: list[str] = []

    def fetch(u):
        fetched.append(u)
        if u.rstrip("/") == SRC.rstrip("/"):
            return source_html
        for root, h in roots.items():
            if u.rstrip("/") == root.rstrip("/"):
                return h
        return None

    cap = LogCap()
    lg = logging.getLogger("web_research")
    prev_level, prev_prop = lg.level, lg.propagate
    lg.setLevel(logging.INFO)
    lg.propagate = False
    lg.addHandler(cap)
    try:
        proj = SimpleNamespace(id=1, maker_name=maker, maker_url=maker_url,
                               source_url=SRC, source_site="wadiz", title="",
                               product_name="", description="", description_clean="")
        r = w.web_research(proj, None, fetch_fn=fetch,
                           search_fn=lambda _q: list(results),
                           max_urls=max_urls if max_urls is not None else w.MAX_URLS,
                           time_budget=120)
    finally:
        lg.removeHandler(cap)
        lg.setLevel(prev_level)
        lg.propagate = prev_prop
    return r, fetched, cap


def official_of(r):
    return r.get("official_site_url") or ""


def n_known_path_fetches(fetched, root):
    """root 配下の代表パスを何本 fetch したか。"""
    base = root.rstrip("/")
    return len([u for u in fetched if any(u == base + p for p in w.WEB_KNOWN_PATHS)])


# ============ 1. expand_known_paths の二重展開防止（間接検証） ============
def test_no_double_expansion():
    print("test_no_double_expansion")
    # 同一 root が複数ページから繰り返し推定されても代表パスは 1 回しか積まれない。
    src = ('<html><body><a href="https://brandk.co.kr">Official</a>'
           '<a href="https://brandk.co.kr/x">more</a></body></html>')
    roots = {"https://brandk.co.kr": html_title("브랜드케이")}
    r, fetched, cap = run(maker="브랜드케이", source_html=src, results=[], roots=roots)
    check("代表パスの展開ログは 1 回だけ", len(cap.expanded) <= 1)
    for p in w.WEB_KNOWN_PATHS:
        u = "https://brandk.co.kr" + p
        check(f"{p} を二重に fetch しない", fetched.count(u) <= 1)


# ============ 2. inferred 候補は検証前に展開しない ============
def test_inferred_defers_known_paths():
    print("test_inferred_defers_known_paths")
    # p96 の実例: 案件ページ由来で support.google.com を公式と誤推定する（inferred=True）。
    # root を取得して major_unrelated_brand:google.com で撤回されるため、代表パスは
    # 1 本も fetch されない（従来は撤回前に 16 本を積んでいた）。
    src = '<html><body><a href="https://support.google.com">Official Website</a></body></html>'
    roots = {"https://support.google.com": html_title("Google Help")}
    r, fetched, cap = run(maker="놀로", source_html=src, results=[], roots=roots)
    check("推定候補は展開を保留する（DEFERRED ログ）", len(cap.deferred) >= 1)
    check("撤回された候補の代表パスを 1 本も fetch しない",
          n_known_path_fetches(fetched, "https://support.google.com") == 0)
    check("root 自体は fetch する（検証のため）",
          any(u.rstrip("/") == "https://support.google.com" for u in fetched))


def test_rejected_candidate_never_expands():
    print("test_rejected_candidate_never_expands")
    # major_unrelated_brand で撤回される候補（p96 の二段目 gemini.google.com）。
    src = '<html><body><a href="https://gemini.google.com">Official</a></body></html>'
    roots = {"https://gemini.google.com": html_title("Gemini")}
    r, fetched, cap = run(maker="놀로", source_html=src, results=[], roots=roots)
    check("大企業ブランド候補は撤回される", not official_of(r))
    check("撤回候補は代表パスを展開しない", len(cap.expanded) == 0)
    check("撤回候補の代表パスを fetch しない",
          n_known_path_fetches(fetched, "https://gemini.google.com") == 0)
    check("撤回理由が major_unrelated_brand",
          any("major_unrelated_brand" in m for m in cap.rejected))


# ============ 3. 検証通過後に 1 回だけ展開 ============
def test_verified_inferred_expands_once():
    print("test_verified_inferred_expands_once")
    # identity が maker と一致する正規候補 → 検証通過後に代表パスを展開する。
    src = '<html><body><a href="https://gswon.com">Official Website</a></body></html>'
    roots = {"https://gswon.com": html_title("경성건강원")}
    r, fetched, cap = run(maker="경성건강원", source_html=src, results=[], roots=roots)
    check("検証通過で公式を採用", official_of(r).startswith("https://gswon.com"))
    check("代表パスを展開する（検証後）", len(cap.expanded) >= 1)
    check("展開は 1 回だけ", len(cap.expanded) == 1)
    check("代表パスを実際に巡回する",
          n_known_path_fetches(fetched, "https://gswon.com") > 0)


# ============ 4. verified 候補（maker_url 由来）は即時展開 ============
def test_maker_url_expands_immediately():
    print("test_maker_url_expands_immediately")
    # maker_url 登録済み＝信頼。検証を待たずに代表パスを積む（従来挙動）。
    roots = {"https://gswon.com": html_title("경성건강원")}
    r, fetched, cap = run(maker="경성건강원", maker_url="https://gswon.com",
                          results=[], roots=roots)
    check("maker_url 由来は公式として確定", official_of(r).startswith("https://gswon.com"))
    check("maker_url 由来は保留しない（DEFERRED を出さない）", len(cap.deferred) == 0)
    check("maker_url 由来は代表パスを巡回する",
          n_known_path_fetches(fetched, "https://gswon.com") > 0)


# ============ 5. root fetch 失敗時の保険 ============
def test_root_fetch_failure_falls_back():
    print("test_root_fetch_failure_falls_back")
    # root は取得不能（None）だが /contact は生きているサイト。検証できないので
    # 保険として代表パスを展開し、recall を落とさない。
    src = '<html><body><a href="https://blockedroot.co.kr">Official</a></body></html>'

    fetched: list[str] = []

    def fetch(u):
        fetched.append(u)
        if u.rstrip("/") == SRC.rstrip("/"):
            return src
        if u == "https://blockedroot.co.kr/contact":
            return html_title("연락처", "") + "<body>info@blockedroot.co.kr</body>"
        return None  # root を含め他は取得不能

    cap = LogCap()
    lg = logging.getLogger("web_research")
    lg.setLevel(logging.INFO); lg.propagate = False; lg.addHandler(cap)
    try:
        proj = SimpleNamespace(id=1, maker_name="블록루트", maker_url="", source_url=SRC,
                               source_site="wadiz", title="", product_name="",
                               description="", description_clean="")
        w.web_research(proj, None, fetch_fn=fetch, search_fn=lambda _q: [],
                       max_urls=w.MAX_URLS, time_budget=120)
    finally:
        lg.removeHandler(cap); lg.propagate = True
    check("root 取得失敗でも代表パスを展開する（保険）", len(cap.expanded) >= 1)
    check("保険でも展開は 1 回だけ", len(cap.expanded) == 1)
    check("/contact を実際に巡回する",
          "https://blockedroot.co.kr/contact" in fetched)


# ============ 6. 撤回候補で URL 予算の消費が減る ============
def test_rejected_candidate_saves_budget():
    print("test_rejected_candidate_saves_budget")
    # 撤回される候補 + 後続の正規候補。代表パスを先払いしないので、後続候補まで
    # 予算が届き公式を採用できる。
    src = '<html><body><a href="https://support.google.com">Official</a></body></html>'
    results = [{"url": "https://gswon.com/", "title": "경성건강원", "snippet": ""}]
    roots = {"https://support.google.com": html_title("Google Help"),
             "https://gswon.com": html_title("경성건강원")}
    r, fetched, cap = run(maker="경성건강원", source_html=src, results=results,
                          roots=roots, max_urls=6)
    check("撤回候補の代表パスで予算を使わない",
          n_known_path_fetches(fetched, "https://support.google.com") == 0)
    check("限られた予算でも正規候補に到達して採用",
          official_of(r).startswith("https://gswon.com"))
    check("URL 予算を超過しない", len(r.get("searched_urls") or []) <= 6)


# ============ 7. p96 回収維持 ============
P96_RESULTS = [
    {"url": "https://linktr.ee/knollo.square", "title": "knollo square Official", "snippet": ""},
    {"url": "https://pf.kakao.com/_QQHub", "title": "카카오톡채널 - 놀로스토어", "snippet": ""},
    {"url": "https://www.knollo.co.kr/", "title": "놀로스퀘어", "snippet": ""},
    {"url": "https://www.knollo.store/", "title": "놀로 knollo", "snippet": ""},
    {"url": "https://platum.kr/archives/199247", "title": "스파크펫, 놀로 출시", "snippet": ""},
    {"url": "https://www.knollo.store/home", "title": "놀로 knollo", "snippet": ""},
    {"url": "https://shoppinglive.naver.com/channels/92191", "title": "놀로스토어", "snippet": ""},
    {"url": "https://m.gsshop.com/section/brandSect/195520", "title": "놀로 - GS SHOP", "snippet": ""},
]
P96_ROOTS = {
    "https://www.knollo.store": html_title("놀로 knollo | 반려동물 간식·용품·케어 전문몰"),
    "https://www.knollo.co.kr": html_title("놀로스퀘어"),
    "https://litt.ly": html_title("리틀리 | 무료로 쉽게 시작하는 나만의 홈페이지"),
}


def test_p96_still_recovered():
    print("test_p96_still_recovered")
    r, _, _ = run(maker="놀로", results=P96_RESULTS, roots=P96_ROOTS)
    got = official_of(r)
    check("p96: 公式を採用（回収を維持）",
          got in ("https://www.knollo.store", "https://www.knollo.co.kr"))
    check("p96: URL 予算を超過しない",
          len(r.get("searched_urls") or []) <= w.MAX_URLS)


# ============ 8. reject / 既存成功ケースの維持 ============
def test_p114_reject_maintained():
    print("test_p114_reject_maintained")
    results = [{"url": "https://lgau.co.kr", "title": "주식회사 올음 아이스크림 메이커", "snippet": ""}]
    roots = {"https://lgau.co.kr": html_title("LG전자 B2B 공식커머셜 전문점 올음")}
    r, _, _ = run(maker="주식회사 올음", results=results, roots=roots)
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
        r, _, _ = run(maker=maker,
                      results=[{"url": root + "/", "title": ident, "snippet": ""}],
                      roots={root: html_title(ident)})
        check(f"{maker}: {root} を採用（維持）", official_of(r) == root)


# ============ 9. ラテン / 日本語 / 中国語 maker の回帰なし ============
def test_non_korean_makers_unaffected():
    print("test_non_korean_makers_unaffected")
    src = '<html><body><a href="https://gearsmith.com">Official Website</a></body></html>'
    r, fetched, cap = run(maker="Gearsmith", source_html=src,
                          results=[{"url": "https://gearsmith.com/", "title": "Gearsmith",
                                    "snippet": ""}],
                          roots={"https://gearsmith.com": html_title("Gearsmith")})
    check("ラテン maker: page 由来公式を採用（維持）",
          official_of(r).startswith("https://gearsmith.com"))
    check("ラテン maker: 検証後に代表パスを展開", len(cap.expanded) >= 1)
    r2, _, _ = run(maker="株式会社ニホンブランド",
                   results=[{"url": "https://nihonbrand.co.jp/",
                             "title": "株式会社ニホンブランド", "snippet": ""}],
                   roots={"https://nihonbrand.co.jp": html_title("株式会社ニホンブランド")})
    check("日本語 maker: 採用を維持", official_of(r2) == "https://nihonbrand.co.jp")
    r3, _, _ = run(maker="裡外生活",
                   results=[{"url": "https://leewayworld.com/", "title": "裡外生活",
                             "snippet": ""}],
                   roots={"https://leewayworld.com": html_title("裡外生活")})
    check("中国語 maker: 採用を維持", official_of(r3) == "https://leewayworld.com")
    r4, _, _ = run(maker="Gearsmith",
                   results=[{"url": "https://otherco.com/", "title": "Gearsmith review",
                             "snippet": ""}],
                   roots={"https://otherco.com": html_title("Other Company")})
    check("identity 不一致は採用しない（言語非依存）", not official_of(r4))


def main():
    test_no_double_expansion()
    test_inferred_defers_known_paths()
    test_rejected_candidate_never_expands()
    test_verified_inferred_expands_once()
    test_maker_url_expands_immediately()
    test_root_fetch_failure_falls_back()
    test_rejected_candidate_saves_budget()
    test_p96_still_recovered()
    test_p114_reject_maintained()
    test_existing_success_cases_maintained()
    test_non_korean_makers_unaffected()
    print(f"\n{_p} passed / {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
