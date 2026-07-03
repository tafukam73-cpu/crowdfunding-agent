"""Japan Opportunity Engine v1-3（ルールベース評価）のオフライン検証。

保存済み発掘商品からルールベースで Japan Opportunity 分析を生成する処理を、
実 API キー・実ネットワークなしで検証する。pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_japan_opportunity_rules.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "japan_opportunity_rules_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.services import discovery_service  # noqa: E402
from app.services import japan_opportunity_service as svc  # noqa: E402

Base.metadata.create_all(engine)

_passed = 0
_failed = 0
_seq = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def _mk(db, **fields):
    """一意な source_url で発掘商品を作成する。"""
    global _seq
    _seq += 1
    data = {"source_platform": "kickstarter",
            "source_url": f"https://kck.st/jor-{_seq}"}
    data.update(fields)
    product, _ = discovery_service.create(db, data)
    return product


def test_small_kitchen_high():
    print("test_small_kitchen_high")
    db = SessionLocal()
    p = _mk(db, product_name="Compact kitchen slicer",
            project_title="Small kitchen gadget for tiny homes",
            category="kitchen",
            description="A compact, lightweight kitchen tool that saves counter "
                        "space and makes prep easy. Perfect for small apartments.",
            status="live")
    a = svc.analyze_product_rules(db, p.id)
    check("小型キッチン用品は japan_market_fit 高評価",
          a.japan_market_fit_score >= 70)
    check("総合スコアが高め", a.overall_opportunity_score >= 55)
    check("規制安全度は高い（要注意カテゴリなし）",
          a.regulatory_safety_score >= 60)
    db.close()


def test_caution_low_regulatory():
    print("test_caution_low_regulatory")
    db = SessionLocal()
    caution = {
        "medical": "A medical diagnostic device for clinics.",
        "supplement": "Daily vitamin supplement with protein powder.",
        "food": "Artisan coffee and snack food subscription.",
        "cosmetics": "Anti-aging skincare serum cosmetic.",
        "wireless": "Wireless bluetooth earbuds with wifi sync.",
        "weapon": "Tactical folding knife and pepper spray weapon kit.",
    }
    for cat, desc in caution.items():
        p = _mk(db, product_name=cat, category=cat, description=desc, status="live")
        a = svc.analyze_product_rules(db, p.id)
        check(f"{cat}: regulatory_safety_score が低い(<50)",
              a.regulatory_safety_score < 50)
    db.close()


def test_discovery_scores_used():
    print("test_discovery_scores_used")
    db = SessionLocal()
    p = _mk(db, product_name="Some product",
            description="A reasonably described product for testing score reuse.",
            japan_fit_score=90, regulatory_risk_score=20, logistics_score=85)
    a = svc.analyze_product_rules(db, p.id)
    check("Discovery の japan_fit_score を japan_market_fit に活用",
          a.japan_market_fit_score == 90)
    check("Discovery の regulatory_risk_score を regulatory_safety に活用",
          a.regulatory_safety_score == 20)
    check("Discovery の logistics_score を活用", a.logistics_score == 85)
    ev = a.evidence_json["discovery_scores_used"]
    check("evidence に使用した Discovery スコアを記録",
          ev["japan_fit_score"] == 90)
    db.close()


def test_funding_backers_boost_cf():
    print("test_funding_backers_boost_cf")
    db = SessionLocal()
    big = _mk(db, product_name="gadget", category="gadget",
              description="A compact gadget with strong crowdfunding traction.",
              funding_amount=600000, backers_count=6000, status="successful")
    small = _mk(db, product_name="gadget", category="gadget",
                description="A compact gadget with strong crowdfunding traction.",
                status="live")
    a_big = svc.analyze_product_rules(db, big.id)
    a_small = svc.analyze_product_rules(db, small.id)
    check("funding/backers 大は crowdfunding_fit が高い",
          a_big.crowdfunding_fit_score > a_small.crowdfunding_fit_score)
    check("funding/backers 大は sales_success も高い",
          a_big.sales_success_score > a_small.sales_success_score)
    db.close()


def test_short_description_low_confidence():
    print("test_short_description_low_confidence")
    db = SessionLocal()
    short = _mk(db, product_name="x", category="kitchen", description="x")
    long = _mk(db, product_name="Compact kitchen tool",
               category="kitchen",
               description="A compact, lightweight kitchen tool that saves counter "
                           "space and is easy to store in small apartments.")
    a_short = svc.analyze_product_rules(db, short.id)
    a_long = svc.analyze_product_rules(db, long.id)
    check("説明が短い商品は confidence が低い",
          a_short.confidence_score < a_long.confidence_score)
    db.close()


def test_failed_canceled_ended_analyzed():
    print("test_failed_canceled_ended_analyzed")
    db = SessionLocal()
    for status in ("failed", "canceled", "ended"):
        p = _mk(db, product_name="Compact kitchen organizer",
                category="kitchen",
                description="A small kitchen storage organizer for tidy counters.",
                status=status)
        a = svc.analyze_product_rules(db, p.id)
        check(f"status={status} でも分析される（除外しない）",
              a.overall_opportunity_score is not None)
    db.close()


def test_evidence_and_range():
    print("test_evidence_and_range")
    db = SessionLocal()
    p = _mk(db, product_name="Compact pet water fountain",
            category="pet",
            description="An automatic compact pet water fountain for cats and dogs.",
            funding_amount=120000, backers_count=1500, status="successful")
    a = svc.analyze_product_rules(db, p.id)
    ev = a.evidence_json
    for key in ("product_category_signals", "risk_signals", "funding_signals",
                "discovery_scores_used", "confidence_factors"):
        check(f"evidence_json に {key} が保存される", key in ev)
    check("funding_signals に funding/backers を記録",
          ev["funding_signals"]["backers_count"] == 1500)
    check("overall_opportunity_score が 0〜100",
          0 <= a.overall_opportunity_score <= 100)
    # 全スコア軸が 0〜100
    from app.schemas.japan_opportunity import SCORE_FIELDS
    in_range = all(
        getattr(a, f) is None or 0 <= getattr(a, f) <= 100 for f in SCORE_FIELDS
    )
    check("全スコア軸が 0〜100 に収まる", in_range)
    db.close()


def test_missing_product():
    print("test_missing_product")
    db = SessionLocal()
    try:
        svc.analyze_product_rules(db, 999999)
        check("存在しない商品は ValueError", False)
    except ValueError:
        check("存在しない商品は ValueError", True)
    db.close()


def test_api():
    print("test_api")
    try:
        from fastapi.testclient import TestClient
        from app.main import app
    except ModuleNotFoundError as exc:
        print(f"  skip- API テストをスキップ（{exc}）")
        return

    client = TestClient(app)
    rp = client.post("/discovery/products", json={
        "source_platform": "kickstarter",
        "source_url": "https://kck.st/jor-api",
        "product_name": "Compact kitchen scale",
        "category": "kitchen",
        "description": "A small, precise kitchen scale for home cooking and baking.",
        "status": "live",
    })
    check("発掘商品作成 200", rp.status_code == 200)
    pid = rp.json()["id"]

    r = client.post(f"/japan-opportunity/analyze/{pid}")
    check("POST /japan-opportunity/analyze/{id} 200", r.status_code == 200)
    body = r.json()
    check("分析にスコアが入る", body["overall_opportunity_score"] is not None)
    check("推奨アクションが入る", bool(body["recommended_next_action"]))
    check("discovered_product_id が紐づく", body["discovered_product_id"] == pid)

    # 最新取得でも同じものが返る
    rl = client.get(f"/japan-opportunity/products/{pid}/latest")
    check("最新分析として取得できる", rl.status_code == 200
          and rl.json()["id"] == body["id"])

    # 存在しない商品は 404
    r404 = client.post("/japan-opportunity/analyze/999999")
    check("存在しない商品は 404", r404.status_code == 404)


def main():
    test_small_kitchen_high()
    test_caution_low_regulatory()
    test_discovery_scores_used()
    test_funding_backers_boost_cf()
    test_short_description_low_confidence()
    test_failed_canceled_ended_analyzed()
    test_evidence_and_range()
    test_missing_product()
    test_api()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
