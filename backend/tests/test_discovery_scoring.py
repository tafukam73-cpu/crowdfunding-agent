"""AI Discovery Scoring Engine（Discovery Engine v1-2）のオフライン検証。

ルールベース評価・AI 注入とフォールバック・正規化・DB 更新・API を sqlite で
検証する。実 API キー不要・ネットワーク不要。pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_discovery_scoring.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "discovery_scoring_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.services import discovery_service as svc  # noqa: E402
from app.services import discovery_scoring_service as scoring  # noqa: E402

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


def _in_range(scores: dict) -> bool:
    return all(
        isinstance(scores[f], int) and 0 <= scores[f] <= 100
        for f in scoring.SCORE_FIELDS
    )


def test_small_kitchen_high():
    print("test_small_kitchen_high")
    scores = scoring.score({
        "category": "kitchen",
        "product_name": "Compact kitchen slicer",
        "project_title": "Small kitchen gadget for tiny homes",
        "description": "A compact, lightweight kitchen tool that saves counter space "
                       "and makes prep easy. Perfect for small apartments.",
        "status": "live",
    })
    check("小型キッチン用品は japan_fit 高評価", scores["japan_fit_score"] >= 70)
    check("crowdfunding_fit も高め", scores["crowdfunding_fit_score"] >= 60)
    check("総合スコアが高め", scores["overall_discovery_score"] >= 60)
    check("規制リスクは低い（=安全度高い）", scores["regulatory_risk_score"] >= 60)


def test_caution_categories_low_regulatory():
    print("test_caution_categories_low_regulatory")
    caution = {
        "medical": "A medical diagnostic device for clinics.",
        "supplement": "Daily vitamin supplement with protein.",
        "food": "Artisan coffee and snack food subscription.",
        "cosmetics": "Anti-aging skincare serum cosmetic.",
        "wireless": "Wireless bluetooth earbuds with wifi sync.",
        "weapon": "Tactical folding knife and pepper spray weapon kit.",
    }
    for cat, desc in caution.items():
        s = scoring.score({"category": cat, "product_name": cat,
                           "description": desc, "status": "live"})
        check(f"{cat}: regulatory_risk_score が低い(<50)",
              s["regulatory_risk_score"] < 50)


def test_normalization():
    print("test_normalization")
    # ルールベース：範囲内
    s = scoring.score({"category": "pet", "description": "A cozy pet bed for dogs."})
    check("ルールベースは 0〜100 に収まる", _in_range(s))
    # AI が範囲外の値を返しても clamp される
    def crazy_ai(_info):
        return {f: 9999 for f in scoring.SCORE_FIELDS}
    s2 = scoring.score({"category": "kitchen"}, ai_fn=crazy_ai)
    check("AI の範囲外値も 0〜100 に正規化", _in_range(s2)
          and s2["japan_fit_score"] == 100)
    # 負値も clamp
    def negative_ai(_info):
        return {f: -50 for f in scoring.SCORE_FIELDS}
    s3 = scoring.score({"category": "kitchen"}, ai_fn=negative_ai)
    check("負値も 0 に正規化", s3["overall_discovery_score"] == 0)


def test_ai_invalid_json_fallback():
    print("test_ai_invalid_json_fallback")
    def broken_ai(_info):
        return "this is not json at all { broken :: "
    s = scoring.score({"category": "kitchen",
                       "product_name": "small kitchen tool",
                       "description": "A compact kitchen gadget for small homes."},
                      ai_fn=broken_ai)
    check("不正 JSON でもフォールバックして全スコアが付く", _in_range(s))
    check("フォールバックでも高評価カテゴリが反映", s["japan_fit_score"] >= 70)

    # AI が例外を投げてもフォールバック
    def raising_ai(_info):
        raise RuntimeError("API down")
    s2 = scoring.score({"category": "pet"}, ai_fn=raising_ai)
    check("AI 例外でもフォールバックする", _in_range(s2))

    # 正常な AI（コードフェンス付き JSON 文字列）はパースされる
    def good_ai(_info):
        return ('```json\n{"japan_fit_score": 88, "overall_discovery_score": 91, '
                '"discovery_reasoning": "AI says great"}\n```')
    s3 = scoring.score({"category": "kitchen"}, ai_fn=good_ai)
    check("正常 AI 応答（```json フェンス）を採用", s3["japan_fit_score"] == 88
          and s3["overall_discovery_score"] == 91)
    check("AI の reasoning を採用", s3["discovery_reasoning"] == "AI says great")


def test_none_and_short_description():
    print("test_none_and_short_description")
    s = scoring.score(None)
    check("None 入力でも落ちず全スコアを返す", _in_range(s))
    s2 = scoring.score({"category": "kitchen", "description": "short"})
    check("短い説明文でも落ちない", _in_range(s2))
    check("短い説明文は reasoning に確信度低を明記", "確信度" in s2["discovery_reasoning"])


def test_ended_failed_canceled_scored():
    print("test_ended_failed_canceled_scored")
    db = SessionLocal()
    for status in ("failed", "canceled", "ended"):
        p, _ = svc.create(db, {
            "source_platform": "kickstarter",
            "source_url": f"https://kck.st/{status}",
            "product_name": "compact kitchen organizer",
            "category": "kitchen",
            "description": "A small kitchen storage organizer for tidy counters.",
            "status": status,
        })
        scored = svc.score_product(db, p.id)
        check(f"status={status} でもスコアリングされる",
              scored.overall_discovery_score is not None)
    db.close()


def test_score_product_updates_db():
    print("test_score_product_updates_db")
    db = SessionLocal()
    p, _ = svc.create(db, {
        "source_platform": "indiegogo",
        "source_url": "https://igg.me/dbupdate",
        "product_name": "small camping storage box",
        "category": "outdoor",
        "description": "A durable, lightweight outdoor storage box for camping trips.",
        "status": "successful",
        "backers_count": 4200,
    })
    check("スコアリング前は未設定", p.overall_discovery_score is None)
    updated = svc.score_product(db, p.id)
    check("score_product で DB が更新される",
          updated.overall_discovery_score is not None
          and updated.japan_fit_score is not None)
    check("reasoning / next_action も保存", bool(updated.discovery_reasoning)
          and bool(updated.recommended_next_action))
    # 別セッションで再読込しても永続化されている
    db2 = SessionLocal()
    reloaded = svc.get(db2, p.id)
    check("別セッションでも永続化", reloaded.overall_discovery_score is not None)
    db2.close()
    check("存在しない id は None", svc.score_product(db, 999999) is None)
    db.close()


def test_auto_score_flag():
    print("test_auto_score_flag")
    db = SessionLocal()
    # auto_score なし（既定 False）→ スコア未設定
    p_off, _ = svc.create(db, {
        "source_platform": "kickstarter",
        "source_url": "https://kck.st/noauto",
        "product_name": "pet water fountain",
        "category": "pet",
        "description": "An automatic pet water fountain for cats and dogs.",
    })
    check("auto_score=False ではスコア未設定", p_off.overall_discovery_score is None)
    # auto_score=True → スコア設定済み
    p_on, _ = svc.create(db, {
        "source_platform": "kickstarter",
        "source_url": "https://kck.st/autoon",
        "product_name": "pet water fountain",
        "category": "pet",
        "description": "An automatic pet water fountain for cats and dogs.",
    }, auto_score=True)
    check("auto_score=True ではスコア設定済み",
          p_on.overall_discovery_score is not None
          and p_on.japan_fit_score is not None)
    db.close()


def test_api_score_endpoint():
    print("test_api_score_endpoint")
    try:
        from fastapi.testclient import TestClient
        from app.main import app
    except ModuleNotFoundError as exc:
        # fastapi 未導入のローカル環境ではスキップ（docker 内では実行される）
        print(f"  skip- API テストをスキップ（{exc}）")
        return

    client = TestClient(app)
    # 作成（auto_score なし）
    r = client.post("/discovery/products", json={
        "source_platform": "kickstarter",
        "source_url": "https://kck.st/api-score",
        "product_name": "compact kitchen scale",
        "category": "kitchen",
        "description": "A small, precise kitchen scale for home cooking and baking.",
        "status": "live",
    })
    check("POST /discovery/products 201/200", r.status_code == 200)
    pid = r.json()["id"]
    check("作成直後はスコア未設定", r.json()["overall_discovery_score"] is None)

    # スコアリング
    r2 = client.post(f"/discovery/products/{pid}/score")
    check("POST /discovery/products/{id}/score 動作", r2.status_code == 200)
    body = r2.json()
    check("スコアが付与される", body["overall_discovery_score"] is not None
          and body["japan_fit_score"] is not None)
    check("reasoning が返る", bool(body["discovery_reasoning"]))

    # auto_score=True の作成
    r3 = client.post("/discovery/products", json={
        "source_platform": "kickstarter",
        "source_url": "https://kck.st/api-auto",
        "product_name": "compact kitchen scale",
        "category": "kitchen",
        "description": "A small, precise kitchen scale for home cooking and baking.",
        "auto_score": True,
    })
    check("auto_score=True 作成でスコア付与",
          r3.json()["overall_discovery_score"] is not None)

    # 存在しない id は 404
    r4 = client.post("/discovery/products/999999/score")
    check("存在しない id は 404", r4.status_code == 404)


def main():
    test_small_kitchen_high()
    test_caution_categories_low_regulatory()
    test_normalization()
    test_ai_invalid_json_fallback()
    test_none_and_short_description()
    test_ended_failed_canceled_scored()
    test_score_product_updates_db()
    test_auto_score_flag()
    test_api_score_endpoint()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
