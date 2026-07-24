"""事実優先表示（予測値の非表示）のオフライン検証（ネットワーク不要）。

このシステムは実務ツールであり、根拠のない予測値をユーザー向け画面に出さない。
API が画面へ返すペイロードに予測スコアが混ざっていないこと、代わりに確認可能な
事実と取得元が返ること、規制を断定しないことを検証する。

  1. 商品ファクトシートに予測スコアが含まれない
  2. 取得できない項目は推測せず「未取得」になる
  3. 各項目に取得元の種類・URL・最終確認日時が付く
  4. AI 生成の文章は ai_generated=True で区別される
  5. 規制は断定せず「確認が必要」＋商品ページ上の根拠語つきで返る
  6. 日本市場確認は「見つからず」を「日本未発売」と断定しない
  7. ゲートの理由はスコアを含まない具体的な文言になる
  8. 一覧・ランキング・Today Tasks・Sales Copilot に事実（facts）が載る
  9. 内部スコアはバックエンドに残っている（ゲート・並び順で使う）
 10. 既存 4 サイトの処理が壊れていない / Ulule が復活していない

実行（backend ディレクトリで）:
    python tests/test_facts_first_ui.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "facts_first_ui_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.project import SALES_TARGET_SITES, Project, SourceSite  # noqa: E402
from app.schemas.project import ProjectOut  # noqa: E402
from app.services import contact_search_gate as gate  # noqa: E402
from app.services import product_context_service as pcs  # noqa: E402
from app.services import product_facts_service as facts  # noqa: E402
from app.services import sales_copilot_service as cp  # noqa: E402
from app.services import workflow_service as wf  # noqa: E402

Base.metadata.create_all(engine)

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


# 画面に出てはいけない予測系のキー。
FORBIDDEN_KEYS = {
    "japan_crowdfunding_score",
    "japan_crowdfunding_threshold",
    "makuake_fit_score",
    "japan_market_fit_score",
    "exclusivity_score",
    "sales_success_score",
    "margin_potential_score",
    "overall_priority_score",
    "sales_value_stars",
    "contactability_score",
}

_seq = 0


_DEFAULT_URL = object()  # 「URL なし」と「既定 URL」を区別するための番兵


def mk(db, *, site="kickstarter", url=_DEFAULT_URL, desc=None, **kw) -> Project:
    global _seq
    _seq += 1
    source_url = (
        f"https://www.kickstarter.com/projects/acme-lab/compact-gadget-{_seq}"
        if url is _DEFAULT_URL
        else url
    )
    p = Project(
        title=kw.pop("title", f"Acme Compact Kitchen Gadget {_seq}"),
        source_site=site,
        source_url=source_url,
        category=kw.pop("category", "kitchen"),
        description=desc or "A compact and portable kitchen gadget with minimal design.",
        description_clean=desc
        or "A compact and portable kitchen gadget with minimal design.",
        currency="USD",
        goal_amount=kw.pop("goal_amount", 10000),
        raised_amount=kw.pop("raised_amount", 45000),
        backers_count=kw.pop("backers_count", 900),
        maker_name="Acme Studio",
        **kw,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _walk_keys(node) -> set:
    """ネストした dict/list から全キーを集める。"""
    out = set()
    if isinstance(node, dict):
        for k, v in node.items():
            out.add(k)
            out |= _walk_keys(v)
    elif isinstance(node, list):
        for v in node:
            out |= _walk_keys(v)
    return out


# --------------------------------------------------------------------------- #
# 1〜4. 商品ファクトシート
# --------------------------------------------------------------------------- #
def test_facts_have_no_predictions():
    print("test_facts_have_no_predictions")
    db = SessionLocal()
    p = mk(db)
    sheet = facts.build(db, p)
    keys = _walk_keys(sheet)
    leaked = keys & FORBIDDEN_KEYS
    check(f"ファクトシートに予測スコアが無い（漏れ: {leaked or 'なし'}）", not leaked)

    labels = {i["label"] for i in sheet["product"]["items"]}
    for expected in ("商品名", "日本語の商品概要", "主な特徴", "商品画像",
                     "海外クラファン商品ページ", "メーカー公式サイト",
                     "対象クラファンサイト", "商品カテゴリー"):
        check(f"商品セクションに「{expected}」がある", expected in labels)

    fund = {i["label"]: i for i in sheet["funding"]["items"]}
    for expected in ("募集開始日", "募集終了日", "募集状況", "残り日数", "目標金額",
                     "調達金額", "支援率", "支援者数", "コメント数", "更新回数"):
        check(f"クラファン実績に「{expected}」がある", expected in fund)
    check("支援率は実績から算出される", fund["支援率"]["value"] == "450%")
    check("支援者数は実績から算出される", fund["支援者数"]["value"] == "900人")
    db.close()


def test_missing_values_are_not_guessed():
    print("test_missing_values_are_not_guessed")
    db = SessionLocal()
    p = mk(db)
    sheet = facts.build(db, p)
    fund = {i["label"]: i for i in sheet["funding"]["items"]}
    check("コメント数は未取得（推測しない）",
          fund["コメント数"]["value"] is None and fund["コメント数"]["status"] == "未取得")
    check("更新回数は未取得（推測しない）", fund["更新回数"]["status"] == "未取得")

    maker = {i["label"]: i for i in sheet["maker"]["items"]}
    for expected in ("所在国", "代表者名", "設立年", "法人情報の確認元"):
        check(f"未取得の「{expected}」は推測されない",
              maker[expected]["value"] is None and maker[expected]["status"] == "未取得")
    db.close()


def test_sources_are_attached():
    print("test_sources_are_attached")
    db = SessionLocal()
    p = mk(db)
    sheet = facts.build(db, p)
    filled = [i for i in sheet["product"]["items"] + sheet["funding"]["items"]
              if i["value"] is not None]
    check("取得済み項目には取得元の種類が付く",
          bool(filled) and all(i["source_kind"] for i in filled))
    check("取得済み項目には最終確認日時が付く",
          all(i["checked_at"] for i in filled))
    campaign = next(i for i in sheet["product"]["items"]
                    if i["label"] == "海外クラファン商品ページ")
    check("取得元URLが商品ページを指す",
          (campaign["source_url"] or "").startswith("https://www.kickstarter.com/"))
    db.close()


def test_ai_summary_is_labelled():
    print("test_ai_summary_is_labelled")
    db = SessionLocal()
    p = mk(db)
    sheet = facts.build(db, p)
    items = {i["label"]: i for i in sheet["product"]["items"]}
    check("日本語概要は AI 生成として区別される", items["日本語の商品概要"]["ai_generated"] is True)
    check("主な特徴は AI 生成として区別される", items["主な特徴"]["ai_generated"] is True)
    check("支援者数などの事実は AI 生成ではない",
          all(i["ai_generated"] is False for i in sheet["funding"]["items"]))
    db.close()


# --------------------------------------------------------------------------- #
# 5. 規制は断定しない
# --------------------------------------------------------------------------- #
def test_regulatory_is_not_asserted():
    print("test_regulatory_is_not_asserted")
    db = SessionLocal()
    p = mk(
        db,
        desc="A rechargeable bluetooth speaker with a lithium battery and AC adapter.",
        title="Acme Bluetooth Speaker",
    )
    reg = facts.regulatory_checks(p)
    items = {r["item"]: r for r in reg["items"]}
    check("無線機能から技適の確認項目が出る", "技適" in items)
    check("電源/バッテリーから PSE の確認項目が出る", "PSE" in items)
    check("断定表現（販売不可・規制対象）を使わない",
          all("販売不可" not in r["message"] and "規制対象" not in r["message"]
              for r in reg["items"]))
    check("「確認が必要」という表現になっている",
          all("確認" in r["message"] for r in reg["items"]))
    check("商品ページ上の根拠語が併記される",
          all(r["evidence_terms"] for r in reg["items"]))
    check("根拠語は実際に本文へ含まれる語",
          "bluetooth" in items["技適"]["evidence_terms"])

    # 根拠が無い商品では確認項目を作らない（推測で増やさない）
    plain = mk(db, desc="A simple wooden storage box for a desk.", title="Acme Wood Box",
               category="storage")
    check("根拠が無ければ規制項目を作らない",
          facts.regulatory_checks(plain)["items"] == [])
    db.close()


# --------------------------------------------------------------------------- #
# 6. 日本市場確認は断定しない
# --------------------------------------------------------------------------- #
def test_japan_market_is_not_asserted():
    print("test_japan_market_is_not_asserted")
    db = SessionLocal()
    p = mk(db)
    jm = facts.japan_market_facts(db, p)
    labels = {i["label"] for i in jm["items"]}
    for expected in ("Amazon.co.jp", "楽天市場", "Yahoo!ショッピング", "Makuake掲載歴",
                     "GREEN FUNDING掲載歴", "CAMPFIRE掲載歴", "日本語公式サイト",
                     "日本の公式販売ページ", "日本代理店"):
        check(f"日本市場確認に「{expected}」がある", expected in labels)
    check("未実施なら checked=False", jm["checked"] is False)
    check("全項目に検索URL（確認元）が付く", all(i["source_url"] for i in jm["items"]))
    check("「日本未発売」と断定しない",
          all("未発売" not in str(i["value"]) for i in jm["items"]))
    check("見つからない場合の文言が用意されている",
          facts.JP_STATUS_LABELS["not_found"] == "確認した範囲では見つからず")
    db.close()


# --------------------------------------------------------------------------- #
# 7. ゲートの理由はスコアを含まない
# --------------------------------------------------------------------------- #
def test_gate_reasons_have_no_score():
    print("test_gate_reasons_have_no_score")
    db = SessionLocal()
    blocked = mk(db, url=None, desc="Support our documentary film and donation drive.",
                 title="Our Documentary Film", category="film")
    result = gate.evaluate(db, blocked, persist=False)
    reasons = result["user_reasons"]
    check("探索しなかった理由が返る", bool(reasons))
    # 「適性82点」のようなスコア表示が理由文に混ざらないこと
    check("理由に適性点数が含まれない",
          all(not re.search(r"\d+\s*点", r) and "スコア" not in r for r in reasons))
    check("具体的な理由（商品ページURL未確認）が含まれる",
          any("商品ページURL未確認" in r for r in reasons))

    ctx = pcs.build(db, blocked)
    check("商品コンテキストに内部スコアが無い",
          not (_walk_keys(ctx) & FORBIDDEN_KEYS))
    check("商品コンテキストは具体的理由を返す", bool(ctx["contact_search_reasons"]))

    dto = ProjectOut.model_validate(blocked)
    check("ProjectOut に内部スコアが無い",
          not hasattr(dto, "japan_crowdfunding_score"))
    db.close()


# --------------------------------------------------------------------------- #
# 8. 画面向けペイロードに事実が載る
# --------------------------------------------------------------------------- #
def test_payloads_carry_facts():
    print("test_payloads_carry_facts")
    db = SessionLocal()
    p = mk(db)

    items = wf.ranking(db, limit=50, status_filter="all")
    check("ランキング全件に facts がある",
          bool(items) and all(i.get("facts") for i in items))
    mine = [i for i in items if i["project_id"] == p.id]
    check("ランキングの facts に支援率が入る",
          bool(mine) and mine[0]["facts"]["funding_rate"] == 450)

    tasks = wf.today_tasks(db, per_group=50)
    flat = [t for g in tasks.values() if isinstance(g, list) for t in g]
    check("今日やること全件に facts がある",
          bool(flat) and all(t.get("facts") for t in flat))

    card = cp.project_copilot(db, p)
    check("Sales Copilot カードに facts がある", bool(card.get("facts")))
    check("Sales Copilot の facts に支援者数が入る",
          card["facts"]["backers_count"] == 900)
    db.close()


# --------------------------------------------------------------------------- #
# 9・10. 内部スコアは残る / 既存サイトは壊れていない
# --------------------------------------------------------------------------- #
def test_internal_scores_kept_and_sites_intact():
    print("test_internal_scores_kept_and_sites_intact")
    db = SessionLocal()
    p = mk(db)
    score, _reasons = gate.japan_crowdfunding_score(db, p)
    check("内部スコアはバックエンドに残っている（ゲート用）", isinstance(score, int))
    check("閾値も残っている", isinstance(gate.JAPAN_CF_SCORE_THRESHOLD, int))
    result = gate.evaluate(db, p, persist=False)
    check("ゲートは内部スコアで判定できる",
          result["japan_crowdfunding_score"] == score)

    values = [s.value for s in SALES_TARGET_SITES]
    for site in ("kickstarter", "indiegogo", "wadiz", "zeczec"):
        check(f"{site} は営業対象サイトのまま", site in values)
    check("Ulule は復活していない", not any(s.name == "ulule" for s in SourceSite))
    db.close()


def main() -> int:
    test_facts_have_no_predictions()
    test_missing_values_are_not_guessed()
    test_sources_are_attached()
    test_ai_summary_is_labelled()
    test_regulatory_is_not_asserted()
    test_japan_market_is_not_asserted()
    test_gate_reasons_have_no_score()
    test_payloads_carry_facts()
    test_internal_scores_kept_and_sites_intact()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
