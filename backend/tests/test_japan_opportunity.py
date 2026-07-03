"""日本市場機会 分析（Japan Opportunity Engine v1-2）のオフライン検証。

分析の作成・正規化・evidence_json 保存・一覧/フィルタ/ソート・最新取得・更新・API を
sqlite で検証する。実 API キー不要・ネットワーク不要。pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_japan_opportunity.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "japan_opportunity_test.sqlite")
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


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def _mk_product(db, url):
    product, _ = discovery_service.create(db, {
        "source_platform": "kickstarter",
        "source_url": url,
        "product_name": "Test Gadget",
        "category": "gadget",
    })
    return product


def test_create():
    print("test_create")
    db = SessionLocal()
    p = _mk_product(db, "https://kck.st/jo-create")
    a = svc.create_analysis(db, p.id, {
        "overall_opportunity_score": 72,
        "japan_market_fit_score": 80,
        "opportunity_reasoning": "小型で日本の住環境に合う",
        "recommended_strategy": "Makuake 先行",
    })
    check("分析を作成できる", a.id is not None)
    check("discovered_product_id が紐づく", a.discovered_product_id == p.id)
    check("総合スコアを保存", a.overall_opportunity_score == 72)
    check("reasoning を保存", a.opportunity_reasoning == "小型で日本の住環境に合う")
    db.close()


def test_missing_product_errors():
    print("test_missing_product_errors")
    db = SessionLocal()
    try:
        svc.create_analysis(db, 999999, {"overall_opportunity_score": 50})
        check("存在しない発掘商品は ValueError", False)
    except ValueError:
        check("存在しない発掘商品は ValueError", True)
    db.close()


def test_score_normalization():
    print("test_score_normalization")
    db = SessionLocal()
    p = _mk_product(db, "https://kck.st/jo-norm")
    a = svc.create_analysis(db, p.id, {
        "overall_opportunity_score": 150,     # 上限超え → 100
        "japan_market_fit_score": -5,         # 下限未満 → 0
        "logistics_score": 55,                # そのまま
        "confidence_score": "abc",            # 非数値 → None
    })
    check("上限超えは 100 に丸め", a.overall_opportunity_score == 100)
    check("下限未満は 0 に丸め", a.japan_market_fit_score == 0)
    check("範囲内はそのまま", a.logistics_score == 55)
    check("非数値は None", a.confidence_score is None)
    db.close()


def test_evidence_json():
    print("test_evidence_json")
    db = SessionLocal()
    p = _mk_product(db, "https://kck.st/jo-evidence")
    evidence = {
        "japan_presence": [{"source": "amazon.co.jp", "hit": False}],
        "axis_confidence": {"logistics_score": 40},
    }
    a = svc.create_analysis(db, p.id, {"evidence_json": evidence})
    check("evidence_json(dict) を保存・取得できる",
          a.evidence_json == evidence)
    # list も許容
    a2 = svc.create_analysis(db, p.id, {"evidence_json": [1, 2, 3]})
    check("evidence_json(list) を保存できる", a2.evidence_json == [1, 2, 3])
    # 別セッションでも永続化
    db2 = SessionLocal()
    reloaded = svc.get_analysis(db2, a.id)
    check("別セッションでも evidence_json が読める",
          reloaded.evidence_json["axis_confidence"]["logistics_score"] == 40)
    db2.close()
    db.close()


def test_list_and_filters():
    print("test_list_and_filters")
    db = SessionLocal()
    p1 = _mk_product(db, "https://kck.st/jo-list1")
    p2 = _mk_product(db, "https://kck.st/jo-list2")
    svc.create_analysis(db, p1.id, {"overall_opportunity_score": 30})
    svc.create_analysis(db, p1.id, {"overall_opportunity_score": 90})
    svc.create_analysis(db, p2.id, {"overall_opportunity_score": 60})

    all_rows = svc.list_analyses(db)
    check("一覧取得できる", len(all_rows) >= 3)

    only_p1 = svc.list_analyses(db, discovered_product_id=p1.id)
    check("discovered_product_id で絞り込める",
          len(only_p1) == 2
          and all(a.discovered_product_id == p1.id for a in only_p1))

    ge50 = svc.list_analyses(db, min_score=50)
    check("min_score で絞り込める",
          all((a.overall_opportunity_score or 0) >= 50 for a in ge50))
    db.close()


def test_sort():
    print("test_sort")
    db = SessionLocal()
    p = _mk_product(db, "https://kck.st/jo-sort")
    svc.create_analysis(db, p.id, {"overall_opportunity_score": 20})
    svc.create_analysis(db, p.id, {"overall_opportunity_score": 95})
    svc.create_analysis(db, p.id, {"overall_opportunity_score": 55})

    by_score = svc.list_analyses(db, discovered_product_id=p.id, sort="score_desc")
    scores = [a.overall_opportunity_score for a in by_score
              if a.overall_opportunity_score is not None]
    check("score_desc でスコア降順", scores == sorted(scores, reverse=True))

    by_created = svc.list_analyses(
        db, discovered_product_id=p.id, sort="created_desc")
    ids = [a.id for a in by_created]
    check("created_desc で新しい順（id 降順）", ids == sorted(ids, reverse=True))
    db.close()


def test_get_latest():
    print("test_get_latest")
    db = SessionLocal()
    p = _mk_product(db, "https://kck.st/jo-latest")
    svc.create_analysis(db, p.id, {"overall_opportunity_score": 40})
    latest_created = svc.create_analysis(db, p.id, {"overall_opportunity_score": 88})
    got = svc.get_latest_for_product(db, p.id)
    check("最新分析を取得できる", got is not None and got.id == latest_created.id)
    check("未分析商品は None",
          svc.get_latest_for_product(db, 987654) is None)
    db.close()


def test_update():
    print("test_update")
    db = SessionLocal()
    p = _mk_product(db, "https://kck.st/jo-update")
    a = svc.create_analysis(db, p.id, {"overall_opportunity_score": 50})
    updated = svc.update_analysis(db, a.id, {
        "overall_opportunity_score": 200,          # 正規化 → 100
        "recommended_next_action": "連絡先探索を開始",
    })
    check("更新できる（スコア正規化）", updated.overall_opportunity_score == 100)
    check("テキストを更新できる",
          updated.recommended_next_action == "連絡先探索を開始")
    check("存在しない分析の更新は None", svc.update_analysis(db, 987654, {}) is None)
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
    # 発掘商品を作成
    rp = client.post("/discovery/products", json={
        "source_platform": "kickstarter",
        "source_url": "https://kck.st/jo-api",
        "product_name": "API Gadget",
        "category": "kitchen",
    })
    check("発掘商品作成 200", rp.status_code == 200)
    pid = rp.json()["id"]

    # 分析を作成
    r = client.post("/japan-opportunity/analyses", json={
        "discovered_product_id": pid,
        "overall_opportunity_score": 130,   # 正規化 → 100
        "japan_market_fit_score": 77,
        "evidence_json": {"note": "api test"},
    })
    check("POST /japan-opportunity/analyses 200", r.status_code == 200)
    body = r.json()
    aid = body["id"]
    check("スコアが正規化される", body["overall_opportunity_score"] == 100)
    check("evidence_json が返る", body["evidence_json"] == {"note": "api test"})

    # 一覧
    rl = client.get(f"/japan-opportunity/analyses?discovered_product_id={pid}")
    check("GET 一覧 200", rl.status_code == 200 and len(rl.json()) >= 1)

    # 詳細
    rg = client.get(f"/japan-opportunity/analyses/{aid}")
    check("GET 詳細 200", rg.status_code == 200 and rg.json()["id"] == aid)

    # 更新
    ru = client.patch(f"/japan-opportunity/analyses/{aid}", json={
        "recommended_strategy": "総代理店",
    })
    check("PATCH 200", ru.status_code == 200
          and ru.json()["recommended_strategy"] == "総代理店")

    # 最新
    rlatest = client.get(f"/japan-opportunity/products/{pid}/latest")
    check("GET 最新 200", rlatest.status_code == 200
          and rlatest.json()["id"] == aid)

    # 存在しない発掘商品は 400
    rbad = client.post("/japan-opportunity/analyses", json={
        "discovered_product_id": 999999,
        "overall_opportunity_score": 50,
    })
    check("存在しない商品は 400", rbad.status_code == 400)

    # 存在しない分析は 404
    r404 = client.get("/japan-opportunity/analyses/999999")
    check("存在しない分析は 404", r404.status_code == 404)

    # 未分析商品の最新は 204
    rp2 = client.post("/discovery/products", json={
        "source_platform": "manual",
        "source_url": "https://kck.st/jo-api-none",
        "product_name": "No Analysis",
    })
    pid2 = rp2.json()["id"]
    r204 = client.get(f"/japan-opportunity/products/{pid2}/latest")
    check("未分析商品の最新は 204", r204.status_code == 204)


def main():
    test_create()
    test_missing_product_errors()
    test_score_normalization()
    test_evidence_json()
    test_list_and_filters()
    test_sort()
    test_get_latest()
    test_update()
    test_api()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
