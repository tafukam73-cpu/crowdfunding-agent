"""Japan Opportunity Engine v1-4（AI評価連携）のオフライン検証。

ai_fn 注入による AI 評価と、AI 失敗時のルールベースへのフォールバックを、実 API
キー・実ネットワークなしで検証する。pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_japan_opportunity_ai.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "japan_opportunity_ai_test.sqlite")
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
    global _seq
    _seq += 1
    data = {"source_platform": "kickstarter",
            "source_url": f"https://kck.st/joa-{_seq}",
            "product_name": "Compact kitchen tool",
            "category": "kitchen",
            "description": "A small lightweight kitchen tool for tidy small kitchens."}
    data.update(fields)
    product, _ = discovery_service.create(db, data)
    return product


def test_ai_valid_json_used():
    print("test_ai_valid_json_used")
    db = SessionLocal()
    p = _mk(db)

    def good_ai(_prompt):
        return {
            "japan_market_fit_score": 88,
            "overall_opportunity_score": 91,
            "confidence_score": 77,
            "opportunity_reasoning": "AI による総合判断",
            "recommended_strategy": "AI 戦略：Makuake 先行",
        }

    a = svc.analyze_product_ai(db, p.id, ai_fn=good_ai)
    check("AI のスコアが採用される", a.japan_market_fit_score == 88
          and a.overall_opportunity_score == 91)
    check("AI の reasoning が採用される", a.opportunity_reasoning == "AI による総合判断")
    check("AI の strategy が採用される",
          a.recommended_strategy == "AI 戦略：Makuake 先行")
    check("evidence.ai_used が True", a.evidence_json["ai_used"] is True)
    db.close()


def test_ai_fenced_json_string():
    print("test_ai_fenced_json_string")
    db = SessionLocal()
    p = _mk(db)

    def fenced_ai(_prompt):
        return ('```json\n{"japan_market_fit_score": 70, '
                '"overall_opportunity_score": 72}\n```')

    a = svc.analyze_product_ai(db, p.id, ai_fn=fenced_ai)
    check("```json フェンス付き文字列を解析して採用",
          a.japan_market_fit_score == 70 and a.overall_opportunity_score == 72)
    check("ai_used True", a.evidence_json["ai_used"] is True)
    db.close()


def test_ai_invalid_json_fallback():
    print("test_ai_invalid_json_fallback")
    db = SessionLocal()
    p = _mk(db)
    rule = svc.analyze_product_rules(db, p.id)  # 比較用ベースライン

    def broken_ai(_prompt):
        return "this is not json at all { broken :: "

    a = svc.analyze_product_ai(db, p.id, ai_fn=broken_ai)
    check("不正 JSON はルールへフォールバック",
          a.evidence_json["ai_used"] is False
          and a.evidence_json["fallback_reason"] == "ai_invalid_json")
    check("スコアはルールベースと一致",
          a.japan_market_fit_score == rule.japan_market_fit_score)
    check("ai_raw_response に生応答を保存",
          a.evidence_json["ai_raw_response"] == "this is not json at all { broken :: ")
    db.close()


def test_ai_exception_fallback():
    print("test_ai_exception_fallback")
    db = SessionLocal()
    p = _mk(db)

    def raising_ai(_prompt):
        raise RuntimeError("API down")

    a = svc.analyze_product_ai(db, p.id, ai_fn=raising_ai)
    check("例外でもフォールバックする", a.evidence_json["ai_used"] is False)
    check("fallback_reason に例外を記録",
          a.evidence_json["fallback_reason"].startswith("ai_exception:"))
    check("スコアは 0〜100", 0 <= a.overall_opportunity_score <= 100)
    db.close()


def test_ai_not_provided_fallback():
    print("test_ai_not_provided_fallback")
    db = SessionLocal()
    p = _mk(db)
    a = svc.analyze_product_ai(db, p.id, ai_fn=None)
    check("ai_fn 未指定でフォールバック", a.evidence_json["ai_used"] is False)
    check("fallback_reason=ai_fn_not_provided",
          a.evidence_json["fallback_reason"] == "ai_fn_not_provided")
    check("ai_raw_response は None",
          a.evidence_json["ai_raw_response"] is None)
    db.close()


def test_ai_out_of_range_normalized():
    print("test_ai_out_of_range_normalized")
    db = SessionLocal()
    p = _mk(db)

    def crazy_ai(_prompt):
        return {
            "japan_market_fit_score": 9999,   # → 100
            "competition_gap_score": -50,     # → 0
            "overall_opportunity_score": 120, # → 100
        }

    a = svc.analyze_product_ai(db, p.id, ai_fn=crazy_ai)
    check("範囲外(上)は 100 に正規化", a.japan_market_fit_score == 100)
    check("範囲外(下)は 0 に正規化", a.competition_gap_score == 0)
    check("overall も 100 に正規化", a.overall_opportunity_score == 100)
    check("AI 採用（範囲外でもフォールバックしない）",
          a.evidence_json["ai_used"] is True)
    db.close()


def test_evidence_fields():
    print("test_evidence_fields")
    db = SessionLocal()
    p = _mk(db)

    def good_ai(_prompt):
        return json.dumps({"overall_opportunity_score": 65})

    a = svc.analyze_product_ai(db, p.id, ai_fn=good_ai)
    ev = a.evidence_json
    for key in ("ai_used", "fallback_reason", "rule_baseline", "ai_raw_response"):
        check(f"evidence_json に {key} が入る", key in ev)
    check("rule_baseline に全スコア軸が入る",
          "japan_market_fit_score" in ev["rule_baseline"]
          and "overall_opportunity_score" in ev["rule_baseline"])
    db.close()


def test_prompt_contents():
    print("test_prompt_contents")
    db = SessionLocal()
    p = _mk(db, product_name="Unique Kitchen Widget 12345")
    captured = {}

    def spy_ai(prompt):
        captured["prompt"] = prompt
        return {"overall_opportunity_score": 60}

    svc.analyze_product_ai(db, p.id, ai_fn=spy_ai)
    prompt = captured.get("prompt", "")
    check("prompt に商品情報（商品名）が含まれる",
          "Unique Kitchen Widget 12345" in prompt)
    check("prompt にルールベース評価が含まれる", "ルールベース評価" in prompt)
    check("prompt に注意書き（予備評価）が含まれる", "予備評価" in prompt)
    check("prompt に JSON 指示が含まれる", "JSON" in prompt)
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
        "source_url": "https://kck.st/joa-api",
        "product_name": "Compact kitchen scale",
        "category": "kitchen",
        "description": "A small precise kitchen scale for home cooking and baking.",
    })
    check("発掘商品作成 200", rp.status_code == 200)
    pid = rp.json()["id"]

    r = client.post(f"/japan-opportunity/analyze-ai/{pid}")
    check("POST /japan-opportunity/analyze-ai/{id} 200", r.status_code == 200)
    body = r.json()
    check("スコアが入る", body["overall_opportunity_score"] is not None)
    check("API 経由は ai_fn 未指定でフォールバック",
          body["evidence_json"]["ai_used"] is False
          and body["evidence_json"]["fallback_reason"] == "ai_fn_not_provided")

    r404 = client.post("/japan-opportunity/analyze-ai/999999")
    check("存在しない商品は 404", r404.status_code == 404)


def main():
    test_ai_valid_json_used()
    test_ai_fenced_json_string()
    test_ai_invalid_json_fallback()
    test_ai_exception_fallback()
    test_ai_not_provided_fallback()
    test_ai_out_of_range_normalized()
    test_evidence_fields()
    test_prompt_contents()
    test_api()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
