"""Lead Qualification Engine（営業対象除外エンジン）判定コアの検証。

このテストは pytest に依存しない（CLAUDE.md §7）。自前の check() で数え、
プロセス終了コード＝失敗件数で合否を表す。

検証の重点は「過剰除外をしないこと」。営業できる案件を止める誤りは画面に出ず
気付けないため、Evidence の無い blocker が作られないこと・推測で停止しないことを
重点的に固定する。

実行: docker compose exec -T backend python tests/test_lead_qualification.py
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["DATABASE_URL"] = (
    f"sqlite:///{Path(tempfile.gettempdir()) / 'lead_qualification.sqlite'}"
)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import lead_qualification_service as lq  # noqa: E402

_passed = _failed = 0

NOW = datetime(2026, 8, 5, 0, 0, 0, tzinfo=timezone.utc)
PRE_R = lq.STAGE_PRE_RESEARCH
PRE_O = lq.STAGE_PRE_OUTREACH


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


# --------------------------------------------------------------------------- #
#  テスト用ヘルパ
# --------------------------------------------------------------------------- #
def iso(days_ago: int = 0) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def base_signals(**over) -> dict:
    """営業可能（clear）になる素直な物理商品。各テストで必要な箇所だけ上書きする。"""
    sig = {
        "project_id": 1,
        "title": "Compact Stainless Steel Water Bottle",
        "description": (
            "A rechargeable stainless steel bottle with a companion app "
            "for temperature control. Waterproof body."
        ),
        "category": "kitchen",
        "campaign_url": "https://www.kickstarter.com/projects/acme/bottle",
        "japanese_summary": "温度管理ができるステンレス製の充電式ボトル。防水の本体を持つ。",
        "maker_name": "Acme Studio",
        "official_site": {
            "url": "https://acme-bottle.com",
            "verified": True,
            "source_url": "https://acme-bottle.com/about",
            "checked_at": iso(10),
            "method": "playwright_fetch",
        },
        "maker_identity": {
            "verified": True,
            "source_url": "https://acme-bottle.com/company",
            "checked_at": iso(10),
            "method": "playwright_fetch",
        },
        "creator_domain": {
            "url": "https://acme-bottle.com",
            "ownership_class": "maker_official",
            "checked_at": iso(10),
            "method": "classify_domain",
        },
        "japan_sales": {
            "status": "completed",
            "result": "not_found_in_japan",
            "confidence": 60,
            "source_urls": ["https://www.amazon.co.jp/s?k=acme+bottle"],
            "checked_at": iso(5),
            "channels": [],
        },
    }
    sig.update(over)
    return sig


def find(result, code: str):
    for f in result.findings:
        if f.code == code:
            return f
    raise AssertionError(f"finding {code} not found")


def sev(result, code: str) -> str:
    return find(result, code).severity


def verdict(result, code: str) -> str:
    return find(result, code).verdict


# --------------------------------------------------------------------------- #
#  1. 型・定数
# --------------------------------------------------------------------------- #
def test_constants():
    print("test_constants")
    check("stage は 2 種", lq.STAGES == ("pre_research", "pre_outreach"))
    check("decision は 3 種", set(lq.DECISIONS) == {"blocked", "review", "clear"})
    check("verdict は 4 種",
          set(lq.VERDICTS) == {"hit", "no_hit", "insufficient_evidence", "stale"})
    check("severity は 3 種", set(lq.SEVERITIES) == {"blocker", "review", "info"})
    check("confidence は 4 種のラベル",
          set(lq.CONFIDENCES) == {"high", "medium", "low", "unverified"})
    check("confidence に数値は含まれない",
          all(isinstance(c, str) and not c.isdigit() for c in lq.CONFIDENCES))
    check("カテゴリは A〜T の 20 種", len(lq.CATEGORY_CODES) == 20)
    check("カテゴリ記号は A〜T",
          set(lq.CATEGORY_CODES) == set("ABCDEFGHIJKLMNOPQRST"))
    check("entity_role に unknown がある", "unknown" in lq.ENTITY_ROLES)
    check("entity_role は 11 種", len(lq.ENTITY_ROLES) == 11)
    check("entity_role に oem/odm/private_label がある",
          {"oem", "odm", "private_label"} <= set(lq.ENTITY_ROLES))
    check("priority_band は値定義のみ（算出関数なし）",
          set(lq.PRIORITY_BAND_VALUES)
          == {"high", "medium", "low", "insufficient_evidence"}
          and not hasattr(lq, "compute_priority_band"))
    check("business value の事実キーは 14 種", len(lq.BUSINESS_VALUE_FACT_KEYS) == 14)
    check("positive_fact の初期対応は 8 種", len(lq.POSITIVE_FACT_LABELS) == 8)
    check("rule_version が定義されている", lq.RULE_VERSION == "lqe-v1")
    check("スコア/確率の公開関数を持たない",
          not any(hasattr(lq, n) for n in
                  ("score", "reply_rate", "success_probability", "total_score")))


# --------------------------------------------------------------------------- #
#  2. Finding / Result 構造
# --------------------------------------------------------------------------- #
def test_structures():
    print("test_structures")
    r = lq.qualify(base_signals(), PRE_R, now=NOW)
    check("QualificationResult が返る", isinstance(r, lq.QualificationResult))
    d = r.to_dict()
    for key in ("project_id", "stage", "decision", "findings", "blocker_codes",
                "review_codes", "positive_facts", "evidence_count",
                "rule_version", "evaluated_at"):
        check(f"Result に {key} がある", key in d)
    check("findings は 20 件（全カテゴリを必ず返す）", len(r.findings) == 20)
    f = d["findings"][0]
    for key in ("code", "key", "label", "stage", "verdict", "severity",
                "confidence", "reason", "evidence", "rule_version"):
        check(f"Finding に {key} がある", key in f)
    check("Finding に補助属性 entity_role がある", "entity_role" in f)
    check("Finding に補助属性 facts がある", "facts" in f)

    ev = lq.Evidence(claim="c", source_url="https://x.example/a",
                     source_kind="k", method="m", checked_at=NOW, excerpt="e")
    for key in ("claim", "source_url", "source_kind", "method", "checked_at",
                "excerpt"):
        check(f"Evidence に {key} がある", key in ev.to_dict())
    check("4 点セットが揃えば complete", ev.is_complete())
    check("source_url 欠落は incomplete",
          not lq.Evidence(claim="c", method="m", checked_at=NOW).is_complete())
    check("checked_at 欠落は incomplete",
          not lq.Evidence(claim="c", source_url="u", method="m").is_complete())
    check("method 欠落は incomplete",
          not lq.Evidence(claim="c", source_url="u", checked_at=NOW).is_complete())
    check("claim 欠落は incomplete",
          not lq.Evidence(claim="", source_url="u", method="m",
                          checked_at=NOW).is_complete())
    check("不正な stage は ValueError",
          _raises(lambda: lq.qualify(base_signals(), "whenever")))


def _raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------------- #
#  3. 素直な案件は clear（過剰除外をしない）
# --------------------------------------------------------------------------- #
def test_clean_project_is_clear():
    print("test_clean_project_is_clear")
    r = lq.qualify(base_signals(), PRE_R, now=NOW)
    check("素直な物理商品は pre_research で clear", r.decision == "clear")
    check("blocker_codes は空", r.blocker_codes == [])
    check("review_codes は空", r.review_codes == [])
    ro = lq.qualify(base_signals(), PRE_O, now=NOW)
    check("素直な物理商品は pre_outreach でも clear", ro.decision == "clear")


# --------------------------------------------------------------------------- #
#  4. カテゴリ別 hit / no_hit / insufficient
# --------------------------------------------------------------------------- #
def test_category_a():
    print("test_category_a  日本市場不適合")
    hit = lq.qualify(base_signals(
        title="Organic Food Snack Box", description="A healthy food snack."), PRE_R,
        now=NOW)
    check("A hit（輸入規制負担カテゴリ）", verdict(hit, "A") == "hit")
    check("A は blocker にならない", sev(hit, "A") != "blocker")
    check("A は pre_research で review", sev(hit, "A") == "review")
    check("A は pre_outreach で info",
          sev(lq.qualify(base_signals(title="Organic Food Snack Box",
                                      description="A healthy food snack."),
                         PRE_O, now=NOW), "A") == "info")
    bulky = lq.qualify(base_signals(description="A large sofa for living rooms."),
                       PRE_R, now=NOW)
    check("A hit（大型・重量物）", verdict(bulky, "A") == "hit")
    check("A no_hit（素直な物理商品）",
          verdict(lq.qualify(base_signals(), PRE_R, now=NOW), "A") == "no_hit")


def test_category_b():
    print("test_category_b  Makuake 向きではない")
    listed = lq.qualify(base_signals(japan_cf_listings=[{
        "url": "https://www.makuake.com/project/acme/",
        "checked_at": iso(3), "method": "brave_search"}]), PRE_R, now=NOW)
    check("B hit（日本CF掲載）", verdict(listed, "B") == "hit")
    check("B は一次証拠があれば blocker", sev(listed, "B") == "blocker")
    check("B の decision は blocked", listed.decision == "blocked")
    no_url = lq.qualify(base_signals(japan_cf_listings=[{"checked_at": iso(3)}]),
                        PRE_R, now=NOW)
    check("B は URL の無い掲載情報では blocker にならない", sev(no_url, "B") != "blocker")
    nonprod = lq.qualify(base_signals(
        title="Online coaching course", description="A coaching membership."),
        PRE_R, now=NOW)
    check("B hit（非物販語）", verdict(nonprod, "B") == "hit")
    check("B 非物販語だけでは review 止まり（市場性の主観で止めない）",
          sev(nonprod, "B") == "review")
    check("B no_hit", verdict(lq.qualify(base_signals(), PRE_R, now=NOW), "B") == "no_hit")


def test_category_c():
    print("test_category_c  OEM の可能性")
    notice = lq.qualify(base_signals(oem_notice={
        "source_url": "https://www.kickstarter.com/projects/acme/bottle",
        "checked_at": iso(1), "method": "campaign_page_parse",
        "excerpt": "ODM partner in Shenzhen"}), PRE_R, now=NOW)
    check("C hit（ページ上の OEM/ODM 記載）", verdict(notice, "C") == "hit")
    check("C は常に review（推測で blocker にしない）", sev(notice, "C") == "review")
    check("C は pre_outreach でも review",
          sev(lq.qualify(base_signals(oem_notice={
              "source_url": "https://x.example/a", "checked_at": iso(1),
              "method": "campaign_page_parse", "excerpt": "ODM"}), PRE_O,
              now=NOW), "C") == "review")
    check("C entity_role=odm を補助属性で返す", find(notice, "C").entity_role == "odm")
    two = lq.qualify(base_signals(other_brand_listings=[
        {"url": "https://a.example/p1", "checked_at": iso(20), "method": "brave_search"},
        {"url": "https://b.example/p2", "checked_at": iso(20), "method": "brave_search"},
    ]), PRE_R, now=NOW)
    check("C hit（別ブランド 2 件）", verdict(two, "C") == "hit")
    check("C entity_role=private_label", find(two, "C").entity_role == "private_label")
    one = lq.qualify(base_signals(other_brand_listings=[
        {"url": "https://a.example/p1", "checked_at": iso(20), "method": "brave_search"},
    ]), PRE_R, now=NOW)
    check("C insufficient（別ブランド 1 件のみ）",
          verdict(one, "C") == "insufficient_evidence")
    check("C insufficient の entity_role は unknown",
          find(one, "C").entity_role == "unknown")
    clean = lq.qualify(base_signals(), PRE_R, now=NOW)
    check("C no_hit", verdict(clean, "C") == "no_hit")
    check("C no_hit の entity_role は unknown", find(clean, "C").entity_role == "unknown")
    check("C は blocker_codes に入らない",
          "C" not in notice.blocker_codes and "C" not in two.blocker_codes)


def _reseller_signals(**over):
    return base_signals(creator_domain={
        "url": "https://reseller-shop.example/store/acme",
        "ownership_class": "distributor",
        "checked_at": iso(30), "method": "classify_domain"}, **over)


def test_category_d():
    print("test_category_d  代理店・販売店のみ")
    r = lq.qualify(_reseller_signals(), PRE_R, now=NOW)
    o = lq.qualify(_reseller_signals(), PRE_O, now=NOW)
    check("D hit（distributor 分類）", verdict(r, "D") == "hit")
    check("D は pre_research で review", sev(r, "D") == "review")
    check("D は pre_outreach で blocker", sev(o, "D") == "blocker")
    check("D pre_research の decision は review", r.decision == "review")
    check("D pre_outreach の decision は blocked", o.decision == "blocked")
    check("D entity_role=distributor", find(o, "D").entity_role == "distributor")
    check("D の証跡に分類元 URL がある",
          find(o, "D").complete_evidence()[0].source_url.startswith("https://reseller-shop"))
    retailer = lq.qualify(base_signals(creator_domain={
        "url": "https://amazon.com/stores/acme", "ownership_class": "retailer",
        "checked_at": iso(30), "method": "classify_domain"}), PRE_O, now=NOW)
    check("D retailer も pre_outreach で blocker", sev(retailer, "D") == "blocker")
    check("D no_hit（maker_official）",
          verdict(lq.qualify(base_signals(), PRE_R, now=NOW), "D") == "no_hit")
    missing = lq.qualify(base_signals(creator_domain=None), PRE_R, now=NOW)
    check("D insufficient（分類未取得）",
          verdict(missing, "D") == "insufficient_evidence")
    check("D insufficient は pre_research で info", sev(missing, "D") == "info")
    check("D insufficient は pre_outreach で review",
          sev(lq.qualify(base_signals(creator_domain=None), PRE_O, now=NOW), "D")
          == "review")


def test_category_e():
    print("test_category_e  メーカー未確認")
    unver = base_signals(maker_identity={"verified": False})
    r = lq.qualify(unver, PRE_R, now=NOW)
    o = lq.qualify(unver, PRE_O, now=NOW)
    check("E hit（identity 未確定）", verdict(r, "E") == "hit")
    check("E は pre_research で info（名前あり）", sev(r, "E") == "info")
    check("E は pre_outreach で blocker", sev(o, "E") == "blocker")
    check("E pre_outreach の decision は blocked", o.decision == "blocked")
    noname = lq.qualify(base_signals(maker_identity={"verified": False},
                                     maker_name=None), PRE_R, now=NOW)
    check("E 名前も無ければ pre_research で review", sev(noname, "E") == "review")
    check("E no_hit（identity 検証済み）",
          verdict(lq.qualify(base_signals(), PRE_O, now=NOW), "E") == "no_hit")
    check("E の証跡は DB 状態（推測URLではない）",
          find(o, "E").complete_evidence()[0].source_url.startswith("db://projects/"))
    check("E の証跡 method は db_state",
          find(o, "E").complete_evidence()[0].method == "db_state")


def _sold_signals(url="https://www.amazon.co.jp/dp/B0TEST", days=5, **over):
    return base_signals(japan_sales={
        "status": "completed", "result": "sold_in_japan", "confidence": 85,
        "source_urls": [url], "checked_at": iso(days),
        "channels": [{"channel": "amazon", "status": "found",
                      "label": "Amazon.co.jp", "search_url": url}]}, **over)


def test_category_f():
    print("test_category_f  既に日本正規販売あり")
    sold = lq.qualify(_sold_signals(), PRE_R, now=NOW)
    check("F hit（sold_in_japan＋販売ページURL）", verdict(sold, "F") == "hit")
    check("F は blocker", sev(sold, "F") == "blocker")
    check("F は pre_outreach でも blocker",
          sev(lq.qualify(_sold_signals(), PRE_O, now=NOW), "F") == "blocker")
    check("F の decision は blocked", sold.decision == "blocked")
    check("F の証跡に販売ページURLがある",
          find(sold, "F").complete_evidence()[0].source_url.endswith("B0TEST"))
    nourl = lq.qualify(base_signals(japan_sales={
        "status": "completed", "result": "sold_in_japan", "confidence": 85,
        "source_urls": [], "checked_at": iso(5), "channels": [
            {"channel": "amazon", "status": "found", "label": "Amazon.co.jp"}]}),
        PRE_R, now=NOW)
    check("F sold_in_japan でも URL 無しなら blocker にしない",
          sev(nourl, "F") != "blocker")
    nf = lq.qualify(base_signals(), PRE_R, now=NOW)
    check("F not_found_in_japan は no_hit", verdict(nf, "F") == "no_hit")
    check("F not_found_in_japan は blocker にしない（不在の証明ではない）",
          sev(nf, "F") == "info")
    inc = lq.qualify(base_signals(japan_sales={
        "status": "completed", "result": "inconclusive", "confidence": 25,
        "source_urls": [], "checked_at": iso(5), "channels": []}), PRE_R, now=NOW)
    check("F inconclusive は insufficient",
          verdict(inc, "F") == "insufficient_evidence")
    check("F inconclusive は pre_research で info", sev(inc, "F") == "info")
    check("F 未実施も insufficient",
          verdict(lq.qualify(base_signals(japan_sales=None), PRE_R, now=NOW), "F")
          == "insufficient_evidence")


def test_categories_g_to_m():
    print("test_categories_g_to_m  規制")
    food = lq.qualify(base_signals(title="Coffee snack food kit",
                                   description="An edible food product."),
                      PRE_R, now=NOW)
    check("H hit（食品）", verdict(food, "H") == "hit")
    check("H は review で blocker にしない", sev(food, "H") == "review")
    check("H の理由に根拠語が入る", "根拠語" in find(food, "H").reason)
    check("H は法令該当を断定しない", "断定ではない" in find(food, "H").reason)

    med = lq.qualify(base_signals(title="Medical therapy device",
                                  description="A clinical diagnos tool."),
                     PRE_R, now=NOW)
    check("I hit（医療）", verdict(med, "I") == "hit")
    check("I は blocker にしない（法令対象を断定しない）", sev(med, "I") != "blocker")

    cos = lq.qualify(base_signals(title="Skincare serum",
                                  description="A cosmetic beauty serum."),
                     PRE_R, now=NOW)
    check("J hit（化粧品）", verdict(cos, "J") == "hit")
    check("J は blocker にしない", sev(cos, "J") != "blocker")

    wifi = lq.qualify(base_signals(
        title="Bluetooth speaker",
        description="A wireless bluetooth speaker with waterproof body."),
        PRE_R, now=NOW)
    check("K hit（無線）", verdict(wifi, "K") == "hit")
    check("K は blocker にしない", sev(wifi, "K") != "blocker")
    check("M hit（K の帰結として技適）", verdict(wifi, "M") == "hit")
    check("M は blocker にしない", sev(wifi, "M") != "blocker")
    check("M の理由に技適が入る", "技適" in find(wifi, "M").reason)
    check("M no_hit（無線語なし）",
          verdict(lq.qualify(base_signals(), PRE_R, now=NOW), "M") == "no_hit")

    batt = lq.qualify(base_signals(
        title="Portable power bank", description="A lithium battery pack."),
        PRE_R, now=NOW)
    check("L hit（電源・PSE）", verdict(batt, "L") == "hit")
    check("L は blocker にしない", sev(batt, "L") != "blocker")
    check("L の理由に PSE が入る", "PSE" in find(batt, "L").reason)

    check("G hit（H〜M の集約）", verdict(food, "G") == "hit")
    check("G は常に info（二重計上しない）", sev(food, "G") == "info")
    check("G no_hit（規制該当なし）",
          verdict(lq.qualify(base_signals(), PRE_R, now=NOW), "G") == "no_hit")
    check("規制は pre_outreach では info",
          sev(lq.qualify(base_signals(title="Coffee snack food kit",
                                      description="An edible food product."),
                         PRE_O, now=NOW), "H") == "info")
    check("規制カテゴリはどのステージでも blocker_codes に入らない",
          not ({"G", "H", "I", "J", "K", "L", "M"} & set(food.blocker_codes)))


def test_categories_n_o_p():
    print("test_categories_n_o_p  非物理")
    saas = lq.qualify(base_signals(
        title="CloudDesk SaaS", description="Our software only web service for teams.",
        japanese_summary="チーム向けのクラウド型ソフトウェアサービス。"), PRE_R, now=NOW)
    check("O hit（SaaS のみ）", verdict(saas, "O") == "hit")
    check("O STRONG 一致は blocker", sev(saas, "O") == "blocker")
    check("O の decision は blocked", saas.decision == "blocked")
    check("O は pre_outreach でも blocker",
          sev(lq.qualify(base_signals(
              title="CloudDesk SaaS",
              description="Our software only web service for teams.",
              japanese_summary="チーム向けのクラウド型ソフトウェア。"),
              PRE_O, now=NOW), "O") == "blocker")

    digital = lq.qualify(base_signals(
        title="Indie documentary film", description="A short film and ebook bundle.",
        japanese_summary="ドキュメンタリー映画と電子書籍のセット企画。"), PRE_R, now=NOW)
    check("N hit（デジタル・コンテンツ）", verdict(digital, "N") == "hit")
    check("N STRONG 一致は blocker", sev(digital, "N") == "blocker")

    service = lq.qualify(base_signals(
        title="Charity fundraiser", description="A donation drive for a nonprofit.",
        japanese_summary="非営利団体のための寄付キャンペーン。"), PRE_R, now=NOW)
    check("P hit（サービス・寄付）", verdict(service, "P") == "hit")
    check("P STRONG 一致は blocker", sev(service, "P") == "blocker")

    weak = lq.qualify(base_signals(
        title="Daily planner app", description="A planner app for your routine.",
        japanese_summary="毎日の予定を管理するプランナーアプリ。"), PRE_R, now=NOW)
    check("O WEAK のみ（物理商品語なし）は insufficient",
          verdict(weak, "O") == "insufficient_evidence")
    check("O WEAK のみは review 止まり（blocker にしない）", sev(weak, "O") == "review")

    clean = lq.qualify(base_signals(), PRE_R, now=NOW)
    for code in ("N", "O", "P"):
        check(f"{code} no_hit（素直な物理商品）", verdict(clean, code) == "no_hit")


def test_companion_app_headphone_not_excluded():
    print("test_companion_app_headphone_not_excluded")
    r = lq.qualify(base_signals(
        title="ANC Headphone Pro",
        description="Wireless headphone with a companion app and EQ presets.",
        japanese_summary="専用アプリでEQ調整ができるノイズキャンセリングヘッドホン。"),
        PRE_R, now=NOW)
    check("companion app 付きヘッドホンは O で止まらない", sev(r, "O") == "info")
    check("companion app 付きヘッドホンは no_hit", verdict(r, "O") == "no_hit")
    check("companion app 付きヘッドホンは blocked にならない", r.decision != "blocked")
    check("理由に物理商品語による打ち消しが記録される",
          "物理商品語" in find(r, "O").reason)


def test_japanese_app_linked_kit_not_excluded():
    print("test_japanese_app_linked_kit_not_excluded")
    r = lq.qualify(base_signals(
        title="スマート水耕栽培キット（アプリ連動）",
        description="本体は充電式。アプリ連動で水位と照明を管理できる栽培キット。",
        japanese_summary="アプリ連動で水位と照明を管理できる充電式の水耕栽培キット。"),
        PRE_R, now=NOW)
    check("アプリ連動の水耕栽培キットは O で止まらない", sev(r, "O") == "info")
    check("アプリ連動の水耕栽培キットは no_hit", verdict(r, "O") == "no_hit")
    check("アプリ連動の水耕栽培キットは blocked にならない", r.decision != "blocked")


def test_category_q():
    print("test_category_q  終売")
    notice = lq.qualify(base_signals(discontinued_notice={
        "source_url": "https://acme-bottle.com/news/discontinued",
        "checked_at": iso(1), "method": "playwright_fetch",
        "excerpt": "This product is discontinued."}), PRE_R, now=NOW)
    check("Q hit（一次証拠あり）", verdict(notice, "Q") == "hit")
    check("Q 一次証拠があれば pre_research で blocker", sev(notice, "Q") == "blocker")
    check("Q は pre_outreach では review",
          sev(lq.qualify(base_signals(discontinued_notice={
              "source_url": "https://acme-bottle.com/news/discontinued",
              "checked_at": iso(1), "method": "playwright_fetch"}), PRE_O,
              now=NOW), "Q") == "review")
    ended = lq.qualify(base_signals(end_date=iso(30), official_site={}),
                       PRE_R, now=NOW)
    check("Q 終了済み＋公式サイト無しは insufficient",
          verdict(ended, "Q") == "insufficient_evidence")
    check("Q 一次証拠なしでは review 止まり", sev(ended, "Q") == "review")
    check("Q no_hit", verdict(lq.qualify(base_signals(), PRE_R, now=NOW), "Q")
          == "no_hit")


def test_category_r():
    print("test_category_r  既に大量流通")
    listings = [{"url": f"https://shop{i}.example/p", "checked_at": iso(10),
                 "method": "brave_search"} for i in range(3)]
    mass = lq.qualify(base_signals(global_listings=listings), PRE_R, now=NOW)
    check("R hit（販売ページ 3 件）", verdict(mass, "R") == "hit")
    check("R は 3 件以上で blocker", sev(mass, "R") == "blocker")
    check("R は pre_outreach では info",
          sev(lq.qualify(base_signals(global_listings=listings), PRE_O, now=NOW), "R")
          == "info")
    two = lq.qualify(base_signals(global_listings=listings[:2]), PRE_R, now=NOW)
    check("R 2 件では insufficient", verdict(two, "R") == "insufficient_evidence")
    check("R 2 件では blocker にしない", sev(two, "R") != "blocker")
    brand = lq.qualify(base_signals(maker_name="Sony"), PRE_R, now=NOW)
    check("R ブランド名一致は hit", verdict(brand, "R") == "hit")
    check("R ブランド名だけでは blocker にしない", sev(brand, "R") == "review")
    check("R no_hit", verdict(lq.qualify(base_signals(), PRE_R, now=NOW), "R")
          == "no_hit")


def test_category_s():
    print("test_category_s  ブランド所有者不明")
    unknown = base_signals(official_site={})
    r = lq.qualify(unknown, PRE_R, now=NOW)
    o = lq.qualify(unknown, PRE_O, now=NOW)
    check("S hit（公式サイト未検証）", verdict(r, "S") == "hit")
    check("S は pre_research で info", sev(r, "S") == "info")
    # 公式サイト未検証は「探索が未完了」を示すことが多く、単独で送信を止める
    # 根拠にはしない（maker 同定は E が別経路で判定する）。
    check("S は pre_outreach で review（単独で送信を止めない）",
          sev(o, "S") == "review")
    check("S だけでは blocked にならない（maker 同定済みなら review 止まり）",
          o.decision == "review")
    cand = lq.qualify(base_signals(official_site={"url": "https://maybe.example",
                                                  "verified": False}), PRE_R, now=NOW)
    check("S 候補ありで未検証は pre_research で review", sev(cand, "S") == "review")
    check("S no_hit（検証済み）",
          verdict(lq.qualify(base_signals(), PRE_O, now=NOW), "S") == "no_hit")


def test_category_t():
    print("test_category_t  情報不足")
    nourl = lq.qualify(base_signals(campaign_url=None,
                                    campaign_url_missing_reason="source_url が未取得"),
                       PRE_R, now=NOW)
    check("T hit（campaign_url 欠落）", verdict(nourl, "T") == "hit")
    check("T は blocker", sev(nourl, "T") == "blocker")
    check("T の decision は blocked", nourl.decision == "blocked")
    check("T の理由に欠落理由が入る", "source_url が未取得" in find(nourl, "T").reason)
    check("T の証跡は DB 状態",
          find(nourl, "T").complete_evidence()[0].method == "db_state")
    short = lq.qualify(base_signals(japanese_summary="短い"), PRE_R, now=NOW)
    check("T hit（日本語概要が不足）", verdict(short, "T") == "hit")
    check("T は pre_outreach でも blocker",
          sev(lq.qualify(base_signals(campaign_url=None), PRE_O, now=NOW), "T")
          == "blocker")
    check("T no_hit", verdict(lq.qualify(base_signals(), PRE_R, now=NOW), "T")
          == "no_hit")
    check("T の閾値は gate と同じ", lq.MIN_SUMMARY_LEN == 20)


# --------------------------------------------------------------------------- #
#  5. 集約の不変条件
# --------------------------------------------------------------------------- #
def test_invariant_evidence_required_for_blocker():
    print("test_invariant_evidence_required_for_blocker")
    # campaign_url が無いと非物理の証跡（商品ページ）が作れない → blocker 降格。
    r = lq.qualify(base_signals(
        title="CloudDesk SaaS", description="Our software only web service.",
        campaign_url=None), PRE_R, now=NOW)
    f = find(r, "O")
    check("証跡が無い O は blocker にならない", f.severity == "review")
    check("降格前の severity が記録される", f.downgraded_from == "blocker")
    check("降格理由に 4 点セットが記録される", "4 点セット" in (f.downgrade_reason or ""))

    manual = lq.Finding(code="F", key="k", label="l", stage=PRE_R, verdict="hit",
                        severity="blocker", confidence="high", reason="r",
                        evidence=[lq.Evidence(claim="c", source_url="u")])
    out = lq._enforce_invariants(manual, NOW)
    check("不完全な証跡だけの blocker は review へ降格", out.severity == "review")

    nohit = lq.Finding(code="F", key="k", label="l", stage=PRE_R,
                       verdict="insufficient_evidence", severity="blocker",
                       confidence="low", reason="r",
                       evidence=[lq.Evidence(claim="c", source_url="u", method="m",
                                             checked_at=NOW)])
    out2 = lq._enforce_invariants(nohit, NOW)
    check("hit 以外の verdict は blocker になれない", out2.severity == "review")
    check("降格理由に verdict が記録される", "verdict=" in (out2.downgrade_reason or ""))


def test_invariant_stale_evidence():
    print("test_invariant_stale_evidence")
    stale = lq.qualify(_sold_signals(days=200), PRE_R, now=NOW)
    f = find(stale, "F")
    check("鮮度切れの F は blocker にならない", f.severity == "review")
    check("verdict が stale になる", f.verdict == "stale")
    check("降格理由に鮮度が記録される", "鮮度切れ" in (f.downgrade_reason or ""))
    fresh = lq.qualify(_sold_signals(days=10), PRE_R, now=NOW)
    check("鮮度内の F は blocker のまま", sev(fresh, "F") == "blocker")
    check("F の鮮度は 90 日", lq._FRESHNESS_DAYS["F"] == 90)
    check("Q の鮮度は 7 日", lq._FRESHNESS_DAYS["Q"] == 7)
    check("既定の鮮度は 365 日", lq._FRESHNESS_DAYS_DEFAULT == 365)
    check("DB 状態の証跡は鮮度切れにならない",
          not lq.Evidence(claim="c", source_url="db://projects/1",
                          method="db_state",
                          checked_at=NOW - timedelta(days=9999)).is_stale(
              now=NOW, max_age_days=1))


def test_decision_is_most_severe_not_sum():
    print("test_decision_is_most_severe_not_sum")
    many_reviews = lq.qualify(base_signals(
        title="Medical skincare food kit",
        description="A clinical cosmetic food supplement kit."), PRE_R, now=NOW)
    check("review が複数あっても blocked にはならない（点数合算しない）",
          many_reviews.decision == "review")
    check("review が複数あることは review_codes に出る",
          len(many_reviews.review_codes) >= 3)
    one_blocker = lq.qualify(_sold_signals(), PRE_R, now=NOW)
    check("blocker が 1 つでもあれば blocked", one_blocker.decision == "blocked")
    check("blocker_codes に F が入る", "F" in one_blocker.blocker_codes)
    check("blocked でも他カテゴリの所見は残る", len(one_blocker.findings) == 20)
    check("info だけなら clear",
          lq.qualify(base_signals(), PRE_R, now=NOW).decision == "clear")


def test_stage_difference_summary():
    print("test_stage_difference_summary")
    sig = base_signals(
        maker_identity={"verified": False}, official_site={},
        creator_domain={"url": "https://shop.example/acme",
                        "ownership_class": "retailer",
                        "checked_at": iso(30), "method": "classify_domain"})
    r = lq.qualify(sig, PRE_R, now=NOW)
    o = lq.qualify(sig, PRE_O, now=NOW)
    check("D/E/S は pre_research で blocker にならない",
          not ({"D", "E", "S"} & set(r.blocker_codes)))
    check("D/E は pre_outreach で blocker になる",
          {"D", "E"} <= set(o.blocker_codes))
    check("S は pre_outreach で review（送信を単独で止めない）",
          "S" in o.review_codes)
    check("pre_research の decision は blocked ではない", r.decision != "blocked")
    check("pre_outreach の decision は blocked", o.decision == "blocked")
    check("stage が Finding に記録される", all(f.stage == PRE_O for f in o.findings))
    check("stage が Result に記録される", o.stage == "pre_outreach")


# --------------------------------------------------------------------------- #
#  6. positive_facts
# --------------------------------------------------------------------------- #
def test_positive_facts():
    print("test_positive_facts")
    r = lq.qualify(base_signals(
        business_emails=[{"email": "hello@acme-bottle.com",
                          "source_url": "https://acme-bottle.com/contact",
                          "checked_at": iso(5), "method": "playwright_fetch"}],
        decision_makers=[{"name": "Jane Doe",
                          "source_url": "https://acme-bottle.com/team",
                          "checked_at": iso(5), "method": "playwright_fetch"}]),
        PRE_R, now=NOW)
    keys = {p.key for p in r.positive_facts}
    for key in ("campaign_url_verified", "physical_product_confirmed",
                "maker_name_present", "official_site_verified",
                "maker_identity_verified", "business_contact_found",
                "decision_maker_found", "japan_sales_check_completed"):
        check(f"positive_fact {key} が付く", key in keys)
    check("positive_fact はすべて証跡を持つ",
          all(p.evidence and all(e.is_complete() for e in p.evidence)
              for p in r.positive_facts))
    check("positive_fact に推測表現が無い",
          not any(w in p.label for p in r.positive_facts
                  for w in ("売れる", "期待", "可能性", "確率")))

    empty = lq.qualify({"project_id": 2, "title": "x"}, PRE_R, now=NOW)
    check("証拠が無ければ positive_facts は空", empty.positive_facts == [])

    noev = lq.qualify(base_signals(
        official_site={"verified": True},
        decision_makers=[{"name": "Ghost"}],
        business_emails=[{"email": "a@b.example"}]), PRE_R, now=NOW)
    k2 = {p.key for p in noev.positive_facts}
    check("source_url の無い公式サイトは positive_fact にしない",
          "official_site_verified" not in k2)
    check("source_url の無い意思決定者は positive_fact にしない",
          "decision_maker_found" not in k2)
    check("source_url の無いメールは positive_fact にしない",
          "business_contact_found" not in k2)

    saas = lq.qualify(base_signals(
        title="CloudDesk SaaS", description="Our software only web service."),
        PRE_R, now=NOW)
    check("非物理商品には physical_product_confirmed を付けない",
          "physical_product_confirmed" not in {p.key for p in saas.positive_facts})


# --------------------------------------------------------------------------- #
#  7. 副作用が無いこと（外部 HTTP / DB 書き込み / URL 生成）
# --------------------------------------------------------------------------- #
def test_no_network_access():
    print("test_no_network_access")

    def boom(*args, **kwargs):
        raise AssertionError("network access attempted")

    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_urlopen = urllib.request.urlopen
    orig_getaddrinfo = socket.getaddrinfo
    socket.socket.connect = boom
    socket.socket.connect_ex = boom
    urllib.request.urlopen = boom
    socket.getaddrinfo = boom
    try:
        for stage in (PRE_R, PRE_O):
            lq.qualify(base_signals(), stage, now=NOW)
            lq.qualify(_sold_signals(), stage, now=NOW)
            lq.qualify(_reseller_signals(), stage, now=NOW)
        ok = True
    except AssertionError:
        ok = False
    finally:
        socket.socket.connect = orig_connect
        socket.socket.connect_ex = orig_connect_ex
        urllib.request.urlopen = orig_urlopen
        socket.getaddrinfo = orig_getaddrinfo
    check("qualify() はネットワークに触れない", ok)


def test_no_side_effect_sources():
    """判定そのものが純粋関数のままであることを固定する。

    PR-2 で永続化（gather_signals / run）が同じモジュールへ入ったため、
    「モジュール全体が DB に触れない」ではなく「判定ロジックが DB に触れない」を
    検証する。DB 書き込みは run() の 1 か所だけに閉じていること。
    """
    print("test_no_side_effect_sources")
    src = Path(lq.__file__).read_text(encoding="utf-8")
    for banned in ("httpx", "requests.", "urllib.request", "playwright"):
        check(f"ソースに {banned} を含まない（外部HTTP禁止）", banned not in src)

    import inspect

    pure = [lq.qualify, lq._enforce_invariants, lq._decide, lq._positive_facts,
            lq._non_physical_analysis] + [
        getattr(lq, n) for n in dir(lq) if n.startswith("_rule_")]
    impure = [fn.__name__ for fn in pure
              if any(t in inspect.getsource(fn)
                     for t in ("db.", "commit(", "session"))]
    check("qualify とルール関数群は DB に触れない", impure == [])
    check("qualify は db 引数を取らない",
          "db" not in lq.qualify.__code__.co_varnames)
    # DB 書き込みは「書き込み専用関数」の中だけに閉じていること。
    # （PR-2: run / _append_history、PR-4: record_override）
    write_fns = [lq.run, lq.record_override, lq._append_history]
    allowed = "".join(inspect.getsource(fn) for fn in write_fns)
    check("db.commit は書き込み関数の中だけ",
          src.count("db.commit()") == allowed.count("db.commit()") > 0)
    check("db.add は書き込み関数の中だけ",
          src.count("db.add(") == allowed.count("db.add(") > 0)
    check("DELETE を行わない", "delete(" not in src and "db.delete" not in src)


def test_no_url_fabrication():
    print("test_no_url_fabrication")
    r = lq.qualify(base_signals(), PRE_R, now=NOW)
    urls = [e.source_url for f in r.findings for e in f.evidence] + [
        e.source_url for p in r.positive_facts for e in p.evidence]
    known = {
        base_signals()["campaign_url"],
        base_signals()["official_site"]["source_url"],
        base_signals()["maker_identity"]["source_url"],
        base_signals()["japan_sales"]["source_urls"][0],
    }
    check("証跡 URL は signals 由来か db:// のみ",
          all(u in known or u.startswith("db://") for u in urls if u))
    guessed = lq.qualify(base_signals(maker_name="Acme"), PRE_R, now=NOW)
    check("maker 名からドメインを組み立てない",
          not any("acme.com" in (e.source_url or "")
                  for f in guessed.findings for e in f.evidence))


def _all_evidence(result):
    return [e for f in result.findings for e in f.evidence] + [
        e for p in result.positive_facts for e in p.evidence]


def test_internal_db_locator_policy():
    """db:// 内部ロケータの取り扱いを固定する（承認済みの運用条件）。"""
    print("test_internal_db_locator_policy")
    check("内部 DB 用の source_kind は internal_db",
          lq.SOURCE_INTERNAL_DB == "internal_db")
    check("旧称 SOURCE_DB_STATE は残していない", not hasattr(lq, "SOURCE_DB_STATE"))

    scenarios = [
        base_signals(),
        base_signals(campaign_url=None),
        base_signals(maker_identity={"verified": False}, official_site={}),
        _sold_signals(),
        _reseller_signals(),
        base_signals(end_date=iso(30), official_site={}),
        base_signals(maker_name="Sony"),
    ]
    locators, kinds_of_internal, bad_shape = [], set(), []
    for sig in scenarios:
        for stage in (PRE_R, PRE_O):
            for ev in _all_evidence(lq.qualify(sig, stage, now=NOW)):
                url = ev.source_url or ""
                if url.startswith("db://"):
                    locators.append(ev)
                    if not url.startswith(f"db://projects/{sig.get('project_id')}#"):
                        bad_shape.append(url)
                if ev.source_kind == lq.SOURCE_INTERNAL_DB:
                    kinds_of_internal.add(ev.method)

    check("内部ロケータの証跡が実際に生成されている", len(locators) > 0)
    check("db:// を使うのは method=db_state のときだけ",
          all(e.method == "db_state" for e in locators))
    check("db:// の source_kind は必ず internal_db",
          all(e.source_kind == lq.SOURCE_INTERNAL_DB for e in locators))
    check("internal_db を名乗る証跡は method=db_state のみ",
          kinds_of_internal <= {"db_state"})
    check("内部ロケータの形は db://projects/<id># のみ（推測生成しない）",
          bad_shape == [])
    check("内部ロケータは http/https の外部リンクではない",
          not any((e.source_url or "").startswith(("http://", "https://"))
                  for e in locators))
    check("内部ロケータは外部証跡の代用にしない（F は db:// を証跡にしない）",
          all(not (e.source_url or "").startswith("db://")
              for e in find(lq.qualify(_sold_signals(), PRE_R, now=NOW), "F").evidence))
    check("外部 URL の証跡に internal_db を使わない",
          all(e.source_kind != lq.SOURCE_INTERNAL_DB
              for e in _all_evidence(lq.qualify(_sold_signals(), PRE_R, now=NOW))
              if (e.source_url or "").startswith("http")))


def test_evidence_count_and_serialization():
    print("test_evidence_count_and_serialization")
    r = lq.qualify(_sold_signals(), PRE_R, now=NOW)
    check("evidence_count は完全な証跡の数", r.evidence_count > 0)
    manual = sum(len(f.complete_evidence()) for f in r.findings) + sum(
        len([e for e in p.evidence if e.is_complete()]) for p in r.positive_facts)
    check("evidence_count が findings＋positive_facts と一致",
          r.evidence_count == manual)
    d = r.to_dict()
    check("to_dict は JSON 化できる型のみ", _json_safe(d))
    check("evaluated_at は ISO8601", d["evaluated_at"].startswith("2026-08-05"))
    check("すべての Finding に rule_version が入る",
          all(f["rule_version"] == "lqe-v1" for f in d["findings"]))


def _json_safe(obj) -> bool:
    import json

    try:
        json.dumps(obj)
    except (TypeError, ValueError):
        return False
    return True


def test_word_boundary_matching():
    print("test_word_boundary_matching")
    check("'app' は 'application' に一致しない", not lq._has_term("this application", "app"))
    check("'app' は 'companion app' に一致する", lq._has_term("companion app", "app"))
    check("'oem' は 'poem' に一致しない", not lq._has_term("a poem", "oem"))
    check("日本語は部分一致で照合する", lq._has_term("寄付を募る", "寄付"))
    r = lq.qualify(base_signals(
        title="Applied ergonomics chair",
        description="An application of ergonomics in a chair.",
        japanese_summary="人間工学を応用した椅子。素材はアルミ。"), PRE_R, now=NOW)
    check("'application' で非物理判定されない", verdict(r, "O") == "no_hit")


def main():
    test_constants()
    test_structures()
    test_clean_project_is_clear()
    test_category_a()
    test_category_b()
    test_category_c()
    test_category_d()
    test_category_e()
    test_category_f()
    test_categories_g_to_m()
    test_categories_n_o_p()
    test_companion_app_headphone_not_excluded()
    test_japanese_app_linked_kit_not_excluded()
    test_category_q()
    test_category_r()
    test_category_s()
    test_category_t()
    test_invariant_evidence_required_for_blocker()
    test_invariant_stale_evidence()
    test_decision_is_most_severe_not_sum()
    test_stage_difference_summary()
    test_positive_facts()
    test_no_network_access()
    test_no_side_effect_sources()
    test_no_url_fabrication()
    test_internal_db_locator_policy()
    test_evidence_count_and_serialization()
    test_word_boundary_matching()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
