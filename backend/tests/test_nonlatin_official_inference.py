"""非ラテン maker 名の公式サイト推定（タイトル照合フォールバック + identity guard / Phase 3-③）。

significant_terms() は ASCII トークン前提のため、韓国語/日本語/中国語の maker 名では
空集合になり _infer_official_url が候補を 1 件も返せない（実測 recall 0%）。
検索結果タイトルには maker 名がそのまま載るのでこれを **最終手段**として使う。

設計上の不変条件（A/B live 検証で発覚した欠陥への対策）:
  ① タイトル候補は最終手段。page 由来 / ドメイン語照合の公式が 1 件でもあれば使わない。
  ② identity guard は候補ドメイン単位。撤回・再評価をまたいでも必ず再検証する。
  ③ 小売モール/求人/書店/portfolio は deny-list で候補にすら入れない。
  ④ identity 不一致 / 取得不能 は採用しない（official_site を昇格させない）。

外部 API / DNS / ネットワーク非依存（fetch/search を注入）。
実行（backend ディレクトリで）:
    python tests/test_nonlatin_official_inference.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import contact_discovery_service as cds  # noqa: E402
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


# ============ 1. _title_official_candidates（候補生成・deny-list） ============
def test_title_candidates():
    print("test_title_candidates")
    cands = [(50, "https://gswon.com"),
             (60, "https://m-shinsegaemall.ssg.com"),
             (55, "https://www.jobkorea.co.kr/company/1"),
             (52, "https://casting.kyobobook.co.kr"),
             (51, "https://www.behance.net/x")]
    titles = {c[1]: "경성건강원" for c in cands}
    titles["https://www.jobkorea.co.kr/company/1"] = "경성건강원 채용"
    got = w._title_official_candidates(cands, titles, "경성건강원")
    check("正規ドメインは候補になる", "https://gswon.com" in got)
    check("小売モール(ssg)は候補から除外", not any("ssg.com" in u for u in got))
    check("求人(jobkorea)は候補から除外", not any("jobkorea" in u for u in got))
    check("書店(kyobobook)は候補から除外", not any("kyobobook" in u for u in got))
    check("portfolio(behance)は候補から除外", not any("behance" in u for u in got))
    # タイトルに maker 名が無ければ候補にしない
    check("タイトル不一致は候補にしない",
          w._title_official_candidates(
              [(50, "https://random.example")], {"https://random.example": "무관"},
              "경성건강원") == [])
    # 法人格のみの短い名前は照合しない
    check("法人格のみの maker 名は候補ゼロ",
          w._title_official_candidates(
              [(50, "https://x.example")], {"https://x.example": "주식회사 아무개"},
              "주식회사") == [])
    # スコア降順で並ぶ（高スコアが先）
    multi = [(10, "https://brandlow.com"), (90, "https://brandhigh.com")]
    tt = {u: "브랜드" for _, u in multi}
    got2 = w._title_official_candidates(multi, tt, "브랜드")
    check("スコア降順（高スコアが先頭）", got2 and got2[0] == "https://brandhigh.com")
    # 同一 URL の重複は 1 件
    dup = [(50, "https://brand.com"), (40, "https://brand.com")]
    check("同一 URL は重複排除",
          w._title_official_candidates(dup, {"https://brand.com": "브랜드"}, "브랜드")
          == ["https://brand.com"])


# ============ 2. identity guard 単体 ============
def test_identity_guard():
    print("test_identity_guard")
    check("identity 一致で True",
          w._official_identity_matches_maker(html_title("경성건강원"), "https://gswon.com", "경성건강원"))
    check("法人格を除いて一致（주식회사 홈시스 ↔ 홈시스）",
          w._official_identity_matches_maker(html_title("홈시스"), "https://h.com", "주식회사홈시스"))
    check("og:site_name で一致", w._official_identity_matches_maker(
        '<html><head><title>Shop</title>'
        '<meta property="og:site_name" content="경성건강원"></head><body>x</body></html>',
        "https://gswon.com", "경성건강원"))
    check("JSON-LD name で一致", w._official_identity_matches_maker(
        '<html><head><title>Shop</title>'
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Organization","name":"경성건강원"}'
        '</script></head><body>x</body></html>', "https://gswon.com", "경성건강원"))
    # 別会社 / 小売は identity に自社名を出す → maker と一致しない
    check("別会社（현대화랑）は identity 不一致で False",
          not w._official_identity_matches_maker(html_title("현대화랑"), "https://x", "이대원"))
    check("小売（키키라운지）は identity 不一致で False",
          not w._official_identity_matches_maker(html_title("키키라운지"), "https://x", "키움하우스"))
    # 小売: 本文に maker 名が出ても identity には出ない
    retail = ("<html><head><title>키키라운지</title></head>"
              "<body>키움하우스 브랜드B 테라</body></html>")
    check("小売: 本文一致では防げないが identity では拒否",
          "키움하우스" in retail
          and not w._official_identity_matches_maker(retail, "https://x", "키움하우스"))
    # identity 取得不能
    check("html 無しは False", not w._official_identity_matches_maker(None, "https://x", "경성건강원"))
    check("空 html は False", not w._official_identity_matches_maker("", "https://x", "경성건강원"))


# ============ 3. エラー/チャレンジページを identity と認めない ============
def test_error_pages():
    print("test_error_pages")
    for t in ["Access Denied", "Just a moment...", "Not Found", "404 Not Found",
              "403 Forbidden", "Attention Required! | Cloudflare",
              "Service Unavailable", "Page Not Found"]:
        check(f"'{t}' を identity として受理しない",
              not w._official_identity_matches_maker(html_title(t), "https://x", "경성건강원"))
    check("'경성건강원 - Not Found' も受理しない",
          not w._official_identity_matches_maker(
              html_title("경성건강원 - Not Found"), "https://x", "경성건강원"))


# ============ e2e 基盤 ============
def make_wr(source_html, roots, search_results, maker, source_url="https://www.wadiz.kr/campaign/1"):
    """web_research を注入 fetch/search で実行。

    source_html: 案件ページ(source_url)の HTML（None 可）
    roots: {root_url: html}  タイトル候補 root の応答
    search_results: [{"url","title","snippet"}]
    """
    def fetch(u):
        if u.rstrip("/") == source_url.rstrip("/"):
            return source_html
        for root, h in roots.items():
            if u.rstrip("/") == root.rstrip("/") or u.startswith(root.rstrip("/") + "/"):
                # root 自身のみ html、サブパスは None（連絡先なし）
                return h if u.rstrip("/") == root.rstrip("/") else None
        return None

    def search(_q):
        return list(search_results)

    proj = SimpleNamespace(id=1, maker_name=maker, maker_url="", source_url=source_url,
                           source_site="wadiz", title="", product_name="")
    return w.web_research(proj, fetch_fn=fetch, search_fn=search)


def official_of(r):
    return r.get("official_site_url") or ""


# ============ 4. e2e: page candidate 優先（リグレッション検知） ============
def test_e2e_page_candidate_priority():
    print("test_e2e_page_candidate_priority")
    # 案件ページが unopenegg.com を公式サイトとしてリンク（page 由来）。
    # 検索結果には別ドメインのタイトル候補（page 由来より後回しであるべき）。
    src = '<html><body><a href="https://unopenegg.com">Official Website</a></body></html>'
    roots = {"https://unopenegg.com": html_title("UNOPENEGG 언오픈에그"),
             "https://portfolio-site.com": html_title("언오픈에그 포트폴리오")}
    results = [
        {"url": "https://www.wadiz.kr/campaign/1", "title": "언오픈에그 캠페인", "snippet": ""},
        {"url": "https://portfolio-site.com", "title": "언오픈에그 작품", "snippet": ""},
    ]
    r = make_wr(src, roots, results, "언오픈에그")
    check("page 由来 unopenegg.com を採用（title 候補を採らない）",
          official_of(r).startswith("https://unopenegg.com"))
    check("title 候補 portfolio-site は採用しない",
          "portfolio-site" not in official_of(r))


def test_e2e_regression_noxglobal():
    print("test_e2e_regression_noxglobal")
    # p98 型: 案件ページが noxglobal.com をリンク。タイトル候補が別に居ても喪失しない。
    src = '<html><body><a href="https://www.noxglobal.com">Official Website</a></body></html>'
    roots = {"https://www.noxglobal.com": html_title("NOX Global 녹프리"),
             "https://m.jobkorea.co.kr/c": html_title("녹프리 채용")}
    results = [
        {"url": "https://www.wadiz.kr/campaign/1", "title": "녹프리 캠페인", "snippet": ""},
        {"url": "https://m.jobkorea.co.kr/c", "title": "녹프리유한회사 채용", "snippet": ""},
    ]
    r = make_wr(src, roots, results, "녹프리유한회사")
    check("page 由来 noxglobal.com を維持（喪失しない）",
          official_of(r).startswith("https://www.noxglobal.com"))


# ============ 5. e2e: タイトル候補が最終手段として採用/撤回 ============
def test_e2e_title_adopted():
    print("test_e2e_title_adopted")
    # page 由来なし。検索タイトル候補 gswon.com、identity 一致 → 採用。
    src = "<html><body>연락처 없음</body></html>"
    roots = {"https://gswon.com": html_title("경성건강원")}
    results = [{"url": "https://gswon.com", "title": "경성건강원", "snippet": "경성건강원 공식몰"}]
    r = make_wr(src, roots, results, "경성건강원")
    check("identity 一致で title 候補を採用", official_of(r).startswith("https://gswon.com"))


def test_e2e_title_rejected_identity():
    print("test_e2e_title_rejected_identity")
    # 別会社（画廊）: identity=현대화랑 ≠ maker 이대원 → 撤回、official は空。
    src = "<html><body>x</body></html>"
    roots = {"https://hyundaihwarang.com": html_title("현대화랑")}
    results = [{"url": "https://hyundaihwarang.com", "title": "Artists 이대원", "snippet": ""}]
    r = make_wr(src, roots, results, "이대원")
    check("別会社は identity 不一致で official 撤回", not official_of(r))


def test_e2e_title_rejected_errorpage():
    print("test_e2e_title_rejected_errorpage")
    src = "<html><body>x</body></html>"
    roots = {"https://gshock.casio.com": html_title("Access Denied")}
    results = [{"url": "https://gshock.casio.com", "title": "뉴 에이지 크리에이터 | THISTIME", "snippet": ""}]
    r = make_wr(src, roots, results, "에이지크리에이터")
    check("Access Denied ページで official 撤回", not official_of(r))


def test_e2e_title_unfetchable():
    print("test_e2e_title_unfetchable")
    # root を取得できない（NXDOMAIN/HTTPS 失敗）→ 採用しない
    src = "<html><body>x</body></html>"
    roots = {}  # renew6.com は fetch で None
    results = [{"url": "https://renew6.com", "title": "리뉴식스", "snippet": ""}]
    r = make_wr(src, roots, results, "리뉴식스")
    check("root 取得不能なら official 採用しない", not official_of(r))


# ============ 6. e2e: 撤回後の再推定（per-domain guard） ============
def test_e2e_retraction_then_reinference():
    print("test_e2e_retraction_then_reinference")
    # 第1候補は identity 不一致（撤回）、第2候補は identity 一致（採用）。
    # 単一 bool だと第1の撤回で guard がバイパスされ第2が無検証採用されるが、
    # per-domain guard なら第2も正しく検証される。スコアで第1を先に評価させる。
    src = "<html><body>x</body></html>"
    roots = {
        "https://otherco.com": html_title("Other Company"),   # 不一致
        "https://gswon.com": html_title("경성건강원"),           # 一致
    }
    results = [
        {"url": "https://otherco.com", "title": "경성건강원 리뷰", "snippet": ""},
        {"url": "https://gswon.com", "title": "경성건강원", "snippet": ""},
    ]
    r = make_wr(src, roots, results, "경성건강원")
    check("第1候補(不一致)を撤回し第2候補(一致)を採用",
          official_of(r).startswith("https://gswon.com"))

    # 逆に、第1が一致・不一致候補が混じっても不一致を採用しないこと
    roots2 = {"https://gswon.com": html_title("경성건강원"),
              "https://badco.com": html_title("Bad Company")}
    results2 = [
        {"url": "https://gswon.com", "title": "경성건강원", "snippet": ""},
        {"url": "https://badco.com", "title": "경성건강원 채용정보", "snippet": ""},
    ]
    r2 = make_wr(src, roots2, results2, "경성건강원")
    check("不一致候補が混在しても maker 一致ドメインを採用",
          official_of(r2).startswith("https://gswon.com"))


def test_e2e_retail_job_book_not_adopted():
    print("test_e2e_retail_job_book_not_adopted")
    # 小売/求人/書店のみが検索に出る（deny-list で候補ゼロ）→ official は空。
    src = "<html><body>x</body></html>"
    for host, title in [("https://m-shinsegaemall.ssg.com", "도서출판 무지개 신세계몰"),
                        ("https://m.jobkorea.co.kr/company", "키움하우스 채용"),
                        ("https://casting.kyobobook.co.kr", "책들의 정원 교보문고")]:
        roots = {host: html_title(title.split()[-1])}
        results = [{"url": host, "title": title, "snippet": ""}]
        r = make_wr(src, roots, results, title.split()[0])
        check(f"{host[:34]} は official にならない", not official_of(r))


# ============ 7. 既存経路の不変性（ラテン名） ============
def test_latin_unaffected():
    print("test_latin_unaffected")
    # ラテン名はドメイン語照合（_infer_official_url）で従来どおり採用され、
    # タイトルフォールバックには入らない。
    got = w._infer_official_url(
        [(50, "https://acmegear.com")], SimpleNamespace(maker_url=None),
        cds.significant_terms("Acme Gear Product"), cds.significant_terms("Acme Gear"), "")
    check("ラテン名はドメイン語照合で採用", got == "https://acmegear.com")
    # _infer_official_url は page_titles を受け取らない（シグネチャ不変性）
    import inspect
    params = list(inspect.signature(w._infer_official_url).parameters)
    check("_infer_official_url に page_titles 引数が無い", "page_titles" not in params)


# ============ 8. 外部大手ブランド支配 identity guard ============
def test_external_brand_guard():
    print("test_external_brand_guard")
    # p114: maker 올음 / identity は LG전자 支配 → 検出語 lg
    b = w._external_brand_in_identity(
        html_title("LG전자 B2B 공식커머셜 전문점 올음"), "https://lgau.co.kr", "주식회사 올음")
    check("p114 LG전자 を検出（brand=lg）", b == "lg")
    # 維持必須の 7 正解 identity は外部ブランドなし
    for mk, ident in [("퍼시몬", "퍼시몬(Persimmon)"), ("경성건강원", "경성건강원"),
                      ("골드뷰티", "주식회사 골드뷰티"), ("도서출판 무지개", "도서출판 무지개"),
                      ("나노랩", "나노랩"), ("어라운드엑스", "어라운드엑스"),
                      ("오피스허브", "오피스허브")]:
        check(f"{mk}: 外部ブランドなし（None）",
              w._external_brand_in_identity(html_title(ident), "https://x", mk) is None)
    # maker 名自身にブランド語が含まれる場合は拒否しない
    check("maker 名に현대 → 拒否しない",
          w._external_brand_in_identity(html_title("현대바이오 홈페이지"), "https://x", "현대바이오") is None)
    check("maker が삼성전자 → 拒否しない",
          w._external_brand_in_identity(html_title("삼성전자 공식몰"), "https://x", "삼성전자") is None)
    # 大文字小文字差を吸収
    check("小文字 lg でも検出",
          w._external_brand_in_identity(html_title("lg전자 리셀러 브랜드씨"), "https://x", "브랜드씨") in ("lg", "lg전자"))
    check("大文字 SAMSUNG でも検出",
          w._external_brand_in_identity(html_title("SAMSUNG Store brandx"), "https://x", "brandx") == "samsung")
    # 韓国語表記・英語表記の両方を検出
    check("韓国語엘지を検出",
          w._external_brand_in_identity(html_title("엘지 대리점 마카"), "https://x", "마카") in ("엘지", "lg"))
    check("英語Appleを検出",
          w._external_brand_in_identity(html_title("Apple Reseller brandy"), "https://x", "brandy") == "apple")
    # identity 空は None（採用側は identity 不一致で別途拒否）
    check("identity 空は None", w._external_brand_in_identity(None, "https://x", "올음") is None)
    # 短いラテン別名(sk)はトークン一致（task/desk 等に誤爆しない）
    check("desk は sk に誤爆しない",
          w._external_brand_in_identity(html_title("standing desk brandz"), "https://x", "brandz") is None)
    check("SK は独立トークンなら検出",
          w._external_brand_in_identity(html_title("SK 매직 대리점 brandw"), "https://x", "brandw") == "sk")


def test_e2e_external_brand_rejected():
    print("test_e2e_external_brand_rejected")
    # title fallback: maker 올음 / 候補 lgau.co.kr identity=LG전자 支配 → 撤回。
    src = "<html><body>x</body></html>"
    roots = {"https://lgau.co.kr": html_title("LG전자 B2B 공식커머셜 전문점 올음")}
    results = [{"url": "https://lgau.co.kr", "title": "주식회사 올음 아이스크림 메이커", "snippet": ""}]
    r = make_wr(src, roots, results, "주식회사 올음")
    check("外部ブランド支配で official 撤回", not official_of(r))


def test_e2e_brand_guard_scope_page_derived():
    print("test_e2e_brand_guard_scope_page_derived")
    # page 由来候補には外部ブランドガードを適用しない（title fallback 専用）。
    # 案件ページが brandq-store.com をリンク（page 由来）。identity に LG があっても採用。
    src = '<html><body><a href="https://brandq-store.com">Official Website</a></body></html>'
    roots = {"https://brandq-store.com": html_title("LG전자 파트너 브랜드큐")}
    results = [{"url": "https://www.wadiz.kr/campaign/1", "title": "브랜드큐 캠페인", "snippet": ""}]
    r = make_wr(src, roots, results, "브랜드큐")
    check("page 由来候補は外部ブランドガードの対象外（採用維持）",
          official_of(r).startswith("https://brandq-store.com"))


def main():
    test_title_candidates()
    test_identity_guard()
    test_error_pages()
    test_e2e_page_candidate_priority()
    test_e2e_regression_noxglobal()
    test_e2e_title_adopted()
    test_e2e_title_rejected_identity()
    test_e2e_title_rejected_errorpage()
    test_e2e_title_unfetchable()
    test_e2e_retraction_then_reinference()
    test_e2e_retail_job_book_not_adopted()
    test_external_brand_guard()
    test_e2e_external_brand_rejected()
    test_e2e_brand_guard_scope_page_derived()
    test_latin_unaffected()
    print(f"\n{_p} passed / {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
