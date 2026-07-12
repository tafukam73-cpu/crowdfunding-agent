"""Discovery Crawler Framework（Discovery Engine v1-3）のオフライン検証。

platform adapter の抽出（JSON / JSON-LD / meta / 埋め込み JSON）、URL 正規化と
重複防止、終了済み/失敗/中止案件の保存、limit、auto_score、/discovery/run API を
sqlite + fixture で検証する。実 API キー不要・実ネットワーク不要。pytest 非依存で
単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_discovery_crawler.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "discovery_crawler_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.models.discovered_product import DiscoveredProduct  # noqa: E402
from app.models.discovery_run import DiscoveryRun  # noqa: E402
from app.services import discovery_crawler_service as crawler  # noqa: E402
from app.services import discovery_service as svc  # noqa: E402
from app.services.discovery_adapters import get_adapter  # noqa: E402
from app.services.discovery_adapters.base import normalize_url  # noqa: E402
from app.services.discovery_adapters.kickstarter_adapter import KickstarterAdapter  # noqa: E402
from app.services.discovery_adapters.indiegogo_adapter import IndiegogoAdapter  # noqa: E402
from app.services.discovery_adapters.backerkit_adapter import BackerkitAdapter  # noqa: E402

Base.metadata.create_all(engine)

_FIX = BACKEND / "tests" / "fixtures" / "discovery"

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


def _fixture(name: str) -> str:
    return (_FIX / name).read_text(encoding="utf-8")


def _fetch_fn(name: str):
    """URL は無視して fixture 本文を返す fetch_fn（ネットワーク不使用）。"""
    text = _fixture(name)
    return lambda _url: text


def _reset(db):
    """テスト間の独立性のため discovered_products / discovery_runs を空にする。"""
    db.query(DiscoveredProduct).delete()
    db.query(DiscoveryRun).delete()
    db.commit()


# --------------------------------------------------------------------------- #
# 1. adapter 単体の抽出
# --------------------------------------------------------------------------- #
def test_manual_adapter_returns_candidates():
    print("test_manual_adapter_returns_candidates")
    adapter = get_adapter("manual")
    cands = adapter.discover(records=[
        {"source_url": "https://example.com/a", "product_name": "Widget A",
         "status": "live"},
        {"source_url": "https://example.com/b", "product_name": "Widget B"},
    ], limit=10)
    check("manual adapter が候補を返す", len(cands) == 2)
    check("manual: 商品名を保持", cands[0].product_name == "Widget A")
    check("manual: platform が manual", cands[0].source_platform == "manual")


def test_kickstarter_adapter_extracts_from_fixture():
    print("test_kickstarter_adapter_extracts_from_fixture")
    adapter = KickstarterAdapter()
    cands = adapter.discover("gadgets", 10, fetch_fn=_fetch_fn("kickstarter_discover.json"))
    check("kickstarter fixture から複数候補を抽出", len(cands) == 5)
    first = cands[0]
    check("タイトルを抽出", first.project_title.startswith("NovaCharge"))
    check("支援額を抽出", str(first.funding_amount) == "184320.5")
    check("支援者数を抽出", int(first.backers_count) == 2143)
    statuses = {c.status for c in cands}
    check("state を共通ステータスへ写像",
          {"successful", "live", "failed", "canceled"} <= statuses)


def test_indiegogo_adapter_extracts_from_fixture():
    print("test_indiegogo_adapter_extracts_from_fixture")
    adapter = IndiegogoAdapter()
    cands = adapter.discover("kitchen", 10, fetch_fn=_fetch_fn("indiegogo_campaign.html"))
    check("indiegogo fixture から候補を抽出", len(cands) == 1)
    c = cands[0]
    check("JSON-LD からタイトル", c.project_title == "PocketPress Compact Espresso")
    check("埋め込み JSON から status", c.status == "live")
    check("埋め込み JSON から支援額", str(c.funding_amount) == "128500.00"
          or str(c.funding_amount) == "128500")
    check("埋め込み JSON から支援者数", int(c.backers_count) == 1830)
    check("画像 URL を抽出", bool(c.image_url))


def test_backerkit_adapter_extracts_from_fixture():
    print("test_backerkit_adapter_extracts_from_fixture")
    adapter = BackerkitAdapter()
    cands = adapter.discover("outdoor", 10, fetch_fn=_fetch_fn("backerkit_project.html"))
    check("backerkit fixture から候補を抽出", len(cands) == 1)
    c = cands[0]
    # JSON-LD 無し → meta タグ + 埋め込み JSON のカスケードで組み立てる
    check("meta からタイトル", c.project_title == "TrailNest Ultralight Tent")
    check("meta から説明", bool(c.description) and "ultralight" in c.description.lower())
    check("埋め込み JSON から status(successful)", c.status == "successful")
    check("埋め込み JSON から支援者数", int(c.backers_count) == 1450)


# --------------------------------------------------------------------------- #
# 2. URL 正規化
# --------------------------------------------------------------------------- #
def test_url_normalization():
    print("test_url_normalization")
    check("トラッキング params を除去",
          normalize_url("https://a.com/p?ref=x&utm_source=y")
          == "https://a.com/p")
    check("末尾スラッシュを畳む",
          normalize_url("https://a.com/p/") == "https://a.com/p")
    check("host を小文字化",
          normalize_url("https://A.COM/P") == "https://a.com/P")
    check("非 URL/空は None", normalize_url("") is None and normalize_url(None) is None)


# --------------------------------------------------------------------------- #
# 3. 保存・重複防止・limit
# --------------------------------------------------------------------------- #
def test_crawler_saves_and_dedupes():
    print("test_crawler_saves_and_dedupes")
    db = SessionLocal()
    _reset(db)
    res = crawler.run(db, source_platform="kickstarter", query="gadgets",
                      limit=10, fetch_fn=_fetch_fn("kickstarter_discover.json"))
    check("found_count=5", res["found_count"] == 5)
    check("saved_count=4（同一URLの重複を1件排除）", res["saved_count"] == 4)
    check("duplicate_count=1", res["duplicate_count"] == 1)
    check("product_ids が 4 件", len(res["product_ids"]) == 4)
    check("run が discovery_runs に記録される", res["run_id"] is not None)
    # トラッキング params が除去されて保存される
    aerodesk = next((p for p in svc.list_products(db)
                     if p.source_url and "aerodesk" in p.source_url), None)
    check("保存 URL からトラッキング除去",
          aerodesk is not None
          and aerodesk.source_url == "https://www.kickstarter.com/projects/aeroworks/aerodesk")

    # 2 回目：全件 DB 重複で新規保存ゼロ
    res2 = crawler.run(db, source_platform="kickstarter", query="gadgets",
                       limit=10, fetch_fn=_fetch_fn("kickstarter_discover.json"))
    check("再実行では二重保存しない（saved=0）", res2["saved_count"] == 0)
    check("再実行は全件 duplicate", res2["duplicate_count"] == 5)
    total = len([p for p in svc.list_products(db)])
    check("DB 上の総数は 4 のまま", total == 4)
    db.close()


def test_limit_is_respected():
    print("test_limit_is_respected")
    db = SessionLocal()
    _reset(db)
    res = crawler.run(db, source_platform="kickstarter", query="gadgets",
                      limit=2, fetch_fn=_fetch_fn("kickstarter_discover.json"))
    check("limit=2 で found_count=2", res["found_count"] == 2)
    check("limit=2 で saved_count=2", res["saved_count"] == 2)
    db.close()


def test_ended_failed_canceled_saved():
    print("test_ended_failed_canceled_saved")
    db = SessionLocal()
    _reset(db)
    # 終了済み各種を manual で投入し、いずれも除外されず保存されることを確認
    records = [
        {"source_url": "https://m/success", "product_name": "S", "status": "successful"},
        {"source_url": "https://m/ended", "product_name": "E", "status": "ended"},
        {"source_url": "https://m/failed", "product_name": "F", "status": "failed"},
        {"source_url": "https://m/canceled", "product_name": "C", "status": "canceled"},
    ]
    res = crawler.run(db, source_platform="manual", records=records, limit=10)
    check("終了済み4種すべて保存", res["saved_count"] == 4)
    saved_statuses = {p.status for p in svc.list_products(db)}
    check("failed が保存される", "failed" in saved_statuses)
    check("canceled が保存される", "canceled" in saved_statuses)
    check("ended が保存される", "ended" in saved_statuses)
    check("successful が保存される", "successful" in saved_statuses)
    db.close()


# --------------------------------------------------------------------------- #
# 4. auto_score
# --------------------------------------------------------------------------- #
def test_auto_score_true_sets_scores():
    print("test_auto_score_true_sets_scores")
    db = SessionLocal()
    _reset(db)
    res = crawler.run(db, source_platform="kickstarter", query="gadgets",
                      limit=3, auto_score=True,
                      fetch_fn=_fetch_fn("kickstarter_discover.json"))
    products = [svc.get(db, pid) for pid in res["product_ids"]]
    check("auto_score=True で全件スコア付与",
          all(p.overall_discovery_score is not None for p in products))
    check("auto_score=True で japan_fit も付与",
          all(p.japan_fit_score is not None for p in products))
    db.close()


def test_auto_score_false_leaves_scores_none():
    print("test_auto_score_false_leaves_scores_none")
    db = SessionLocal()
    _reset(db)
    res = crawler.run(db, source_platform="kickstarter", query="gadgets",
                      limit=3, auto_score=False,
                      fetch_fn=_fetch_fn("kickstarter_discover.json"))
    products = [svc.get(db, pid) for pid in res["product_ids"]]
    check("auto_score=False ではスコア未設定",
          all(p.overall_discovery_score is None for p in products))
    db.close()


# --------------------------------------------------------------------------- #
# 5. API
# --------------------------------------------------------------------------- #
def test_run_api_endpoint():
    print("test_run_api_endpoint")
    try:
        from fastapi.testclient import TestClient
        from app.main import app
    except ModuleNotFoundError as exc:
        print(f"  skip- API テストをスキップ（{exc}）")
        return

    client = TestClient(app)

    # /discovery/run はジョブ方式：準備中プラットフォーム（backerkit）は 400 で弾く。
    r = client.post("/discovery/run", json={
        "source_platform": "backerkit", "limit": 5,
    })
    check("準備中プラットフォームの run は 400", r.status_code == 400)

    # manual も実取得対応ではないため 400（実行不可）。
    r_manual = client.post("/discovery/run", json={
        "source_platform": "manual", "limit": 5,
    })
    check("manual run は 400（実取得非対応）", r_manual.status_code == 400)

    # GET /discovery/platforms が対応状況を返す（UI の単一の真実源）。
    rp = client.get("/discovery/platforms")
    check("GET /discovery/platforms 200", rp.status_code == 200)
    avail = {p["platform"]: p for p in rp.json()}
    check("wadiz/zeczec が実取得対応",
          avail["wadiz"]["available"] and avail["zeczec"]["available"])
    check("backerkit は準備中", avail["backerkit"]["available"] is False)
    check("wadiz は検索クエリ非対応（一覧取得型）",
          avail["wadiz"]["query_supported"] is False)

    # 存在しないジョブは 404。
    rj = client.get("/discovery/jobs/99999")
    check("未知ジョブは 404", rj.status_code == 404)

    # 既存 /discovery/products 系が壊れていないこと
    r3 = client.get("/discovery/products")
    check("既存 GET /discovery/products が動作", r3.status_code == 200)
    r4 = client.post("/discovery/products", json={
        "source_platform": "kickstarter",
        "source_url": "https://kck.st/coexist-check",
        "product_name": "compact kitchen tool",
        "category": "kitchen",
    })
    check("既存 POST /discovery/products が動作", r4.status_code == 200)


def main():
    test_manual_adapter_returns_candidates()
    test_kickstarter_adapter_extracts_from_fixture()
    test_indiegogo_adapter_extracts_from_fixture()
    test_backerkit_adapter_extracts_from_fixture()
    test_url_normalization()
    test_crawler_saves_and_dedupes()
    test_limit_is_respected()
    test_ended_failed_canceled_saved()
    test_auto_score_true_sets_scores()
    test_auto_score_false_leaves_scores_none()
    test_run_api_endpoint()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
