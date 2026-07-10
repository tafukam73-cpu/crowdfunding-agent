"""営業適性アセスメント（Sales Copilot v2 基盤）のオフライン検証。

実ネットワーク/DB 不要。ルールベースの 3 スコア（日本市場適性 / 独占販売可能性 /
Makuake 適性）が入力シグナルに対して妥当に反応することを純粋関数で検証する。

実行（backend ディレクトリで）:
    python tests/test_sales_assessment.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import sales_assessment_service as sa  # noqa: E402

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


def _sig(**kw):
    base = dict(
        title="", description="", category="", raised=None, goal=None,
        rate_pct=None, backers=None, maker_name="", has_official_site=False,
        has_contact=False, japan_checked=False, japan_stars=None,
        already_in_japan=False, on_makuake=False, source_site="zeczec",
    )
    base.update(kw)
    return base


# 理想案件: 物販ガジェット・強い実績・日本未上陸・中小メーカー・連絡先あり
_STRONG = _sig(
    title="Compact mini projector", description="portable compact gadget for home cinema, design",
    category="design goods", raised=180000, goal=50000, rate_pct=360, backers=250,
    maker_name="green river studio", has_official_site=True, has_contact=True,
    japan_checked=True, japan_stars=5, already_in_japan=False, on_makuake=False,
)
# 弱い案件: 体験/サービス・実績薄・個人・データ不足
_WEAK = _sig(
    title="World cup campaign plan", description="support our athlete campaign to the world cup",
    category="社會", raised=60000, goal=30000, rate_pct=200, backers=4,
    maker_name="individual", has_official_site=False, has_contact=False,
    japan_checked=False,
)


def test_japan_market_fit():
    print("test_japan_market_fit")
    r = sa.score_japan_market_fit(_STRONG)
    check("物販ガジェットは高スコア", r["score"] >= 65)
    check("level=high", r["level"] == "high")
    check("subscores を持つ", set(r["subscores"]) == {"category_appeal", "traction", "regulatory_ease"})
    # 規制の重いカテゴリ（医療/食品）は下がる
    med = sa.score_japan_market_fit(_sig(title="medical therapy device", description="clinical medical",
                                         category="medical", rate_pct=300, backers=100))
    check("規制カテゴリは市場適性を下げる", med["score"] < r["score"])
    check("規制理由を残す", any("規制" in x or "要注意" in x for x in med["reasons"]))


def test_exclusivity():
    print("test_exclusivity")
    strong = sa.score_exclusivity(_STRONG)
    check("未上陸+中小+連絡先ありは高スコア", strong["score"] >= 70)
    # 大企業は独占取りにくい
    lg = sa.score_exclusivity(_sig(maker_name="LG", japan_checked=True, japan_stars=5,
                                   has_contact=True))
    check("大企業は独占スコアが低い", lg["subscores"]["maker_size"] <= 20)
    check("大企業スコア < 中小スコア", lg["score"] < strong["score"])
    # 既に日本販売なら独占余地が小さい
    injp = sa.score_exclusivity(_sig(maker_name="small co", already_in_japan=True,
                                     japan_checked=True, japan_stars=1, has_contact=True))
    check("日本既販売は独占余地が小さい", injp["subscores"]["japan_openness"] <= 25)


def test_makuake_fit():
    print("test_makuake_fit")
    strong = sa.score_makuake_fit(_STRONG)
    check("物販+実績+新規は高スコア", strong["score"] >= 60)
    # 体験/サービスは不向き
    weak = sa.score_makuake_fit(_WEAK)
    check("体験/サービス型は低スコア", weak["score"] < 50)
    check("非物販の理由を残す", any("物販" in x or "不向き" in x for x in weak["reasons"]))
    # 既に Makuake 掲載なら新規性が低い
    onmk = sa.score_makuake_fit(_sig(title="gadget", category="small gadget",
                                     rate_pct=300, backers=200, on_makuake=True))
    check("Makuake 既掲載は新規性が低い", onmk["subscores"]["japan_novelty"] <= 20)


def test_assess_overall_and_confidence():
    print("test_assess_overall_and_confidence")
    strong = sa.assess(_STRONG)
    weak = sa.assess(_WEAK)
    check("総合優先度: 強 > 弱", strong["overall_priority_score"] > weak["overall_priority_score"])
    check("全スコア 0..100", all(
        0 <= strong[k]["score"] <= 100 for k in ("japan_market_fit", "exclusivity", "makuake_fit")))
    check("充足案件の confidence が高い", strong["confidence"] >= 70)
    check("データ不足の confidence が低い", weak["confidence"] < strong["confidence"])
    check("engine を持つ", strong["engine"] == sa.RULE_ENGINE_NAME)


def test_deterministic():
    print("test_deterministic")
    a = sa.assess(_STRONG)
    b = sa.assess(_STRONG)
    check("同入力は同結果（決定的）",
          a["overall_priority_score"] == b["overall_priority_score"])


def test_no_data_is_neutral_not_zero():
    print("test_no_data_is_neutral_not_zero")
    empty = sa.assess(_sig())
    check("空入力でも 0 固定にせず中立域", 20 <= empty["overall_priority_score"] <= 70)
    check("空入力は低 confidence", empty["confidence"] <= 45)


if __name__ == "__main__":
    test_japan_market_fit()
    test_exclusivity()
    test_makuake_fit()
    test_assess_overall_and_confidence()
    test_deterministic()
    test_no_data_is_neutral_not_zero()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
