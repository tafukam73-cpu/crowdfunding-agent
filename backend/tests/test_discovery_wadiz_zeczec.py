"""Wadiz / Zeczec を Discovery（商品発掘）へ接続した実装のオフライン検証。

既存の Wadiz / Zeczec スクレイパーの ``normalize()`` を実データ形の入力に適用して
``ProjectCreate`` を作り、それを ``ScraperBackedAdapter`` が ``DiscoveryCandidate``
→ discovered_products dict へ正しく変換するかを検証する。保存・重複防止・スコアリング・
発掘ジョブ（discovery_job_service）も sqlite + fake scraper で確認する。

実 API キー不要・実ネットワーク不要。pytest 非依存で単体実行できる（既存テストと同方式）。

実行（backend ディレクトリで）:
    python tests/test_discovery_wadiz_zeczec.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "discovery_wz_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.models.discovered_product import DiscoveredProduct  # noqa: E402
from app.models.discovery_job import DiscoveryJob, DiscoveryJobStatus  # noqa: E402
from app.models.discovery_run import DiscoveryRun  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.scrapers import registry as scraper_registry  # noqa: E402
from app.scrapers.wadiz import normalize as wadiz_normalize  # noqa: E402
from app.scrapers.zeczec import normalize as zeczec_normalize  # noqa: E402
from app.services import discovery_crawler_service as crawler  # noqa: E402
from app.services import discovery_job_service as jobsvc  # noqa: E402
from app.services import discovery_scoring_service as scoring  # noqa: E402
from app.services import discovery_service as svc  # noqa: E402
from app.services.discovery_adapters import (  # noqa: E402
    WadizAdapter,
    ZeczecAdapter,
    is_live_fetch,
    platform_availability,
    query_supported,
)

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


def _reset(db):
    db.query(DiscoveredProduct).delete()
    db.query(DiscoveryRun).delete()
    db.query(DiscoveryJob).delete()
    db.query(Project).delete()
    db.commit()


# 実データ形の Wadiz funding API アイテム（1 件）。
_WADIZ_ITEM = {
    "id": 4242,
    "title": "스마트 텀블러 · 온도 유지 보온병",
    "linkUrl": "/campaign/detail/4242",
    "categoryName": "주방가전",
    "amount": 15396000,
    "rate": 3079,           # 達成率（整数%）
    "participants": 177,    # 支援者数（Wadiz は participants）
    "makerName": "주식회사 올음",
    "thumbnail": "https://cdn.wadiz.kr/img/4242.jpg",
    "productType": "REWARD",
    "remainingDay": 10,
    "startDate": "2026-06-01T00:00:00",
    "endDate": "2026-07-31T00:00:00",
}

# 実データ形の Zeczec 一覧カード（1 件・メーカー名/カテゴリは一覧に無い＝null）。
_ZECZEC_CARD = {
    "url": "https://www.zeczec.com/projects/board-game-guild",
    "title": "《冒險少女公會》擴充包Vol.1",
    "img": "https://cdn.zeczec.com/board.jpg",
    "raised": "239059",
    "rate": "239",
    "backers": "163",
    "days": "20",
}


# --------------------------------------------------------------------------- #
# 1. Platform registry
# --------------------------------------------------------------------------- #
def test_platform_registry():
    print("test_platform_registry")
    avail = {p.platform: p for p in platform_availability()}
    check("wadiz available=true", avail["wadiz"].available is True)
    check("zeczec available=true", avail["zeczec"].available is True)
    check("indiegogo available=true（現行実装どおり）", avail["indiegogo"].available is True)
    check("backerkit available=false（準備中）", avail["backerkit"].available is False)
    # manual は発掘元一覧に載せない（available 判定は False）
    check("manual is_live_fetch=false", is_live_fetch("manual") is False)
    check("wadiz query_supported=false", query_supported("wadiz") is False)
    check("zeczec query_supported=false", query_supported("zeczec") is False)
    # Kickstarter の既存状態を壊さない（実取得対応・検索クエリ対応）
    check("kickstarter available=true", avail["kickstarter"].available is True)
    check("kickstarter query_supported=true", avail["kickstarter"].query_supported is True)


# --------------------------------------------------------------------------- #
# 2. Wadiz adapter：ProjectCreate → Discovery DTO
# --------------------------------------------------------------------------- #
def test_wadiz_adapter_dto():
    print("test_wadiz_adapter_dto")
    pc = wadiz_normalize(_WADIZ_ITEM)
    cand = WadizAdapter()._to_candidate(pc)
    d = cand.to_product_dict(default_platform="wadiz")

    check("source_platform=wadiz", d["source_platform"] == "wadiz")
    check("title を保持", d["project_title"] == _WADIZ_ITEM["title"])
    check("product_name を保持", d["product_name"] == _WADIZ_ITEM["title"])
    check("source_url を保持", d["source_url"].endswith("/campaign/detail/4242"))
    check("description（メモ）を保持", bool(d["description"]) and "[Wadiz]" in d["description"])
    check("image_url を保持", d["image_url"] == _WADIZ_ITEM["thumbnail"])
    check("category=categoryName", d["category"] == "주방가전")
    check("maker_name=makerName", d["creator_name"] == "주식회사 올음")
    check("raised_amount を保持", str(d["funding_amount"]) == "15396000.00")
    check("currency=KRW", d["currency"] == "KRW")
    check("backers=participants(177)", d["backers_count"] == 177)
    check("status=live（memo の Status から正規化）", d["status"] == "live")

    # achievement_rate（%）= raised/goal×100。goal は rate から逆算されるため rate に一致。
    ar = scoring.achievement_rate(d)
    check("achievement_rate が算出され rate(3079%) 近傍", ar is not None and abs(ar - 3079) <= 1)

    # raw_data（Wadiz 固有：amount / rate / participants / categoryName / makerName）
    raw = d["raw_data"]
    check("raw_data.amount", raw and raw.get("amount") == "15396000")
    check("raw_data.rate(3079)", raw.get("rate") == 3079)
    check("raw_data.participants(177)", raw.get("participants") == 177)
    check("raw_data.categoryName", raw.get("categoryName") == "주방가전")
    check("raw_data.makerName", raw.get("makerName") == "주식회사 올음")
    check("raw_data.currency=KRW", raw.get("currency") == "KRW")


def test_wadiz_adapter_null_preserved():
    print("test_wadiz_adapter_null_preserved")
    # メーカー名・カテゴリ・画像が欠けたアイテム → 推測せず null を維持する。
    item = {"id": 9, "title": "미니 가방", "linkUrl": "/campaign/detail/9",
            "amount": 500000, "rate": 120, "participants": 12, "productType": "REWARD",
            "remainingDay": 3}
    pc = wadiz_normalize(item)
    d = WadizAdapter()._to_candidate(pc).to_product_dict(default_platform="wadiz")
    check("maker_name 欠損は null", d["creator_name"] is None)
    check("category 欠損は null", d["category"] is None)
    check("image_url 欠損は null", d["image_url"] is None)
    check("currency は KRW（推測ではなくスクレイパー既定）", d["currency"] == "KRW")


# --------------------------------------------------------------------------- #
# 3. Zeczec adapter：ProjectCreate → Discovery DTO（enrichment あり/なし）
# --------------------------------------------------------------------------- #
def test_zeczec_adapter_dto():
    print("test_zeczec_adapter_dto")
    pc = zeczec_normalize(_ZECZEC_CARD)
    d = ZeczecAdapter()._to_candidate(pc).to_product_dict(default_platform="zeczec")

    check("source_platform=zeczec", d["source_platform"] == "zeczec")
    check("title を保持", d["project_title"] == _ZECZEC_CARD["title"])
    check("source_url を保持", d["source_url"].endswith("/projects/board-game-guild"))
    check("currency=TWD", d["currency"] == "TWD")
    check("raised_amount を保持", str(d["funding_amount"]) == "239059.00")
    check("backers(163)", d["backers_count"] == 163)
    # 一覧カードにメーカー名/カテゴリは無い → 推測せず null。
    check("maker_name 欠損は null（推測しない）", d["creator_name"] is None)
    check("category 欠損は null（推測しない）", d["category"] is None)

    raw = d["raw_data"]
    check("raw_data.amount", raw.get("amount") == "239059")
    check("raw_data.rate(239)", raw.get("rate") == 239)
    check("raw_data.backers(163)", raw.get("backers") == 163)
    check("raw_data.currency=TWD", raw.get("currency") == "TWD")
    check("enrichment 無しでも raw_data.enrichment は無い", "enrichment" not in raw)

    ar = scoring.achievement_rate(d)
    check("achievement_rate が rate(239%) 近傍", ar is not None and abs(ar - 239) <= 1)


def test_zeczec_adapter_with_enrichment():
    print("test_zeczec_adapter_with_enrichment")
    pc = zeczec_normalize(_ZECZEC_CARD)
    enrichment = {
        "maker_name": "MORESIE",
        "category": "挺好店",
        "description": "台湾発のデザイン雑貨。確認済みの商品説明テキスト。",
        "official_site_candidates": [
            {"url": "https://moresie.example", "confidence": "high"}
        ],
        "official_site_url": "https://moresie.example",
    }
    cand = ZeczecAdapter()._to_candidate(pc, enrichment=enrichment)
    d = cand.to_product_dict(default_platform="zeczec")
    check("enrichment.maker_name を反映", d["creator_name"] == "MORESIE")
    check("enrichment.category を反映", d["category"] == "挺好店")
    check("enrichment.description を反映", d["description"] == enrichment["description"])
    check("enrichment.official_site を maker_url に反映",
          d["official_website_url"] == "https://moresie.example")
    enr = (d["raw_data"] or {}).get("enrichment") or {}
    check("raw_data.enrichment.official_site_candidates を保持",
          isinstance(enr.get("official_site_candidates"), list)
          and enr["official_site_candidates"][0]["url"] == "https://moresie.example")


def test_zeczec_adapter_enrichment_none_ok():
    print("test_zeczec_adapter_enrichment_none_ok")
    # enrichment=None でもエラーにならず null で返る。
    pc = zeczec_normalize(_ZECZEC_CARD)
    cand = ZeczecAdapter()._to_candidate(pc, enrichment=None)
    check("enrichment None でも変換成功", cand is not None)
    check("maker_name は null のまま", cand.creator_name is None)


# --------------------------------------------------------------------------- #
# 4. 保存・重複防止・projects へ誤保存しない（crawler + fake scraper）
# --------------------------------------------------------------------------- #
class _FakeScraper:
    def __init__(self, projects):
        self._projects = projects

    def scrape(self):
        return list(self._projects)


class _RaisingScraper:
    def scrape(self):
        raise RuntimeError("Wadiz[empty_result]: 0 件（テスト）")


def _patch_scraper(projects):
    scraper_registry.get_scraper = lambda site, limit=20: _FakeScraper(projects)


def _patch_raising():
    scraper_registry.get_scraper = lambda site, limit=20: _RaisingScraper()


def test_save_dedupe_and_no_project_write():
    print("test_save_dedupe_and_no_project_write")
    db = SessionLocal()
    _reset(db)
    projects = [wadiz_normalize(_WADIZ_ITEM), wadiz_normalize(
        {**_WADIZ_ITEM, "id": 5, "linkUrl": "/campaign/detail/5", "title": "다른 상품"}
    )]
    _patch_scraper(projects)
    res = crawler.run(db, source_platform="wadiz", limit=10, auto_score=True)
    check("fetched=2", res["found_count"] == 2)
    check("saved=2", res["saved_count"] == 2)
    check("duplicate=0", res["duplicate_count"] == 0)
    check("scored=2（全件スコア付与）", res["scored_count"] == 2)
    check("failed=0", res["failed_count"] == 0)
    check("product_ids を 2 件返す", len(res["product_ids"]) == 2)
    check("discovered_products に 2 件保存", db.query(DiscoveredProduct).count() == 2)
    # projects へ誤保存しない（発掘は discovered_products のみ）。
    check("projects へ誤保存しない", db.query(Project).count() == 0)

    # 再取得：同一 source_url は duplicate、二重保存しない。
    res2 = crawler.run(db, source_platform="wadiz", limit=10, auto_score=True)
    check("再取得 saved=0", res2["saved_count"] == 0)
    check("再取得 duplicate=2 に増える", res2["duplicate_count"] == 2)
    check("DB 総数は 2 のまま", db.query(DiscoveredProduct).count() == 2)
    db.close()


def test_no_empty_overwrite():
    print("test_no_empty_overwrite")
    db = SessionLocal()
    _reset(db)
    # 1 回目：完全なアイテムを保存。
    _patch_scraper([wadiz_normalize(_WADIZ_ITEM)])
    crawler.run(db, source_platform="wadiz", limit=5, auto_score=True)
    p = db.query(DiscoveredProduct).one()
    check("初回 maker_name あり", p.creator_name == "주식회사 올음")
    check("初回 currency=KRW", p.currency == "KRW")
    # 2 回目：同一 URL・maker 欠落のアイテムでも既存を空値で上書きしない
    #（discovery_service.create は重複時に既存を返し更新しない）。
    stripped = {**_WADIZ_ITEM}
    stripped.pop("makerName")
    stripped.pop("categoryName")
    _patch_scraper([wadiz_normalize(stripped)])
    crawler.run(db, source_platform="wadiz", limit=5, auto_score=True)
    p2 = db.query(DiscoveredProduct).one()
    check("既存 maker_name が空値で上書きされない", p2.creator_name == "주식회사 올음")
    check("既存 category が空値で上書きされない", p2.category == "주방가전")
    db.close()


def test_zero_result_not_success():
    print("test_zero_result_not_success")
    db = SessionLocal()
    _reset(db)
    _patch_raising()
    res = crawler.run(db, source_platform="wadiz", limit=5, auto_score=True)
    check("0 件取得は success 扱いにしない", res["status"] != "success")
    check("saved=0", res["saved_count"] == 0)
    check("error_message を残す", bool(res["error_message"]))
    db.close()


# --------------------------------------------------------------------------- #
# 5. Scoring（Wadiz/Zeczec が一律 0 点にならない・通貨・欠損）
# --------------------------------------------------------------------------- #
def test_scoring_not_all_zero():
    print("test_scoring_not_all_zero")
    wadiz = wadiz_normalize(_WADIZ_ITEM)
    wd = WadizAdapter()._to_candidate(wadiz).to_product_dict(default_platform="wadiz")
    sw = scoring.score(wd)
    check("Wadiz overall > 0", sw["overall_discovery_score"] > 0)
    check("Wadiz japan_fit > 0", sw["japan_fit_score"] > 0)

    zeczec = zeczec_normalize(_ZECZEC_CARD)
    zd = ZeczecAdapter()._to_candidate(zeczec).to_product_dict(default_platform="zeczec")
    sz = scoring.score(zd)
    check("Zeczec overall > 0", sz["overall_discovery_score"] > 0)
    check("Zeczec japan_fit > 0", sz["japan_fit_score"] > 0)


def test_scoring_missing_fields_not_zero():
    print("test_scoring_missing_fields_not_zero")
    # category 欠損のみ → 0 点にならない。
    s1 = scoring.score({"category": None, "product_name": "compact organizer",
                        "description": "A compact reusable kitchen organizer for small spaces.",
                        "currency": "USD"})
    check("category 欠損だけで 0 点にならない", s1["overall_discovery_score"] > 0)
    # description 欠損のみ → 0 点にならない（confidence 低下のみ）。
    s2 = scoring.score({"category": "kitchen", "product_name": "mini tool",
                        "description": None, "currency": "USD"})
    check("description 欠損だけで 0 点にならない", s2["overall_discovery_score"] > 0)
    # データ不足（ほぼ空）→ 0 点にはせず中立寄り（confidence 低下）。
    s3 = scoring.score({})
    check("データ不足でも 0 点ではない", s3["overall_discovery_score"] > 0)


def test_scoring_currency_not_confused():
    print("test_scoring_currency_not_confused")
    base = {"category": "gadget", "product_name": "widget",
            "description": "A neat small gadget for everyday use, compact and light.",
            "funding_amount": 200000, "funding_goal": 100000}
    usd = scoring.score({**base, "currency": "USD"})
    krw = scoring.score({**base, "currency": "KRW"})
    twd = scoring.score({**base, "currency": "TWD"})
    # 200,000 USD は「大型調達」加点、200,000 KRW(≈$150) は加点なし。
    check("KRW を USD として過大評価しない",
          usd["crowdfunding_fit_score"] > krw["crowdfunding_fit_score"])
    check("TWD を USD として過大評価しない",
          usd["crowdfunding_fit_score"] > twd["crowdfunding_fit_score"])


def test_scoring_participants_as_backers():
    print("test_scoring_participants_as_backers")
    # adapter は participants を backers_count にマップする。支援者数が多いほど CF 適性↑。
    high = scoring.score({"category": "gadget", "description": "x" * 60,
                          "backers_count": 5000, "currency": "KRW"})
    low = scoring.score({"category": "gadget", "description": "x" * 60,
                         "backers_count": None, "currency": "KRW"})
    check("participants(=backers) 多で CF 適性が上がる",
          high["crowdfunding_fit_score"] > low["crowdfunding_fit_score"])


def test_achievement_rate_normalized():
    print("test_achievement_rate_normalized")
    # 達成率は raised/goal×100 の float に正規化される（通貨に依存しない比率）。
    r = scoring.achievement_rate({"funding_amount": 150000, "funding_goal": 100000})
    check("achievement_rate=150.0", r == 150.0)
    check("goal 無しは None", scoring.achievement_rate({"funding_amount": 100}) is None)


# --------------------------------------------------------------------------- #
# 6. Discovery job
# --------------------------------------------------------------------------- #
def test_job_completed_lifecycle():
    print("test_job_completed_lifecycle")
    db = SessionLocal()
    _reset(db)
    _patch_scraper([wadiz_normalize(_WADIZ_ITEM)])
    # runner=_run_job_inner で同期実行（fake scraper・fetch 注入なし）。
    job, is_new = jobsvc.create_job(
        db, source_platform="wadiz", query=None, limit=5,
        runner=jobsvc._run_job_inner,
    )
    check("新規ジョブ作成", is_new is True)
    db.refresh(job)
    check("完了時 status=completed", job.status == DiscoveryJobStatus.completed.value)
    check("完了時 found_count=1", job.found_count == 1)
    check("完了時 saved_count=1", job.saved_count == 1)
    check("完了時 product_ids を保持", isinstance(job.product_ids, list) and len(job.product_ids) == 1)
    check("完了時 scored_count=1", job.scored_count == 1)
    db.close()


def test_job_failed_keeps_error():
    print("test_job_failed_keeps_error")
    db = SessionLocal()
    _reset(db)
    _patch_raising()
    job, _ = jobsvc.create_job(
        db, source_platform="wadiz", query=None, limit=5,
        runner=jobsvc._run_job_inner,
    )
    db.refresh(job)
    # crawler は例外を握って status=error・saved=0 を返す → ジョブは failed。
    check("失敗時 status=failed", job.status == DiscoveryJobStatus.failed.value)
    check("失敗時 error を保持", bool(job.error))
    db.close()


def test_job_dedupe_active():
    print("test_job_dedupe_active")
    db = SessionLocal()
    _reset(db)
    # queued の既存ジョブを手で挿入（進行中とみなす）。
    existing = DiscoveryJob(
        source_platform="zeczec", query=None, limit=6,
        status=DiscoveryJobStatus.queued.value, progress=0, product_ids=[],
    )
    db.add(existing)
    db.commit()
    db.refresh(existing)

    called = {"n": 0}

    def _noop_runner(_job_id):
        called["n"] += 1

    job, is_new = jobsvc.create_job(
        db, source_platform="zeczec", query=None, limit=6, runner=_noop_runner
    )
    check("同一 platform×query×limit の active job を再利用", job.id == existing.id)
    check("重複ジョブを作らない（is_new=False）", is_new is False)
    check("再利用時は runner を呼ばない", called["n"] == 0)
    check("discovery_jobs は 1 行のまま", db.query(DiscoveryJob).count() == 1)

    # 条件が違えば別ジョブを作る（limit 変更）。
    job2, is_new2 = jobsvc.create_job(
        db, source_platform="zeczec", query=None, limit=7, runner=_noop_runner
    )
    check("条件が違えば新規ジョブ", is_new2 is True and job2.id != existing.id)
    db.close()


def test_get_job_does_not_start():
    print("test_get_job_does_not_start")
    db = SessionLocal()
    _reset(db)
    j = DiscoveryJob(source_platform="wadiz", limit=5,
                     status=DiscoveryJobStatus.queued.value, progress=0, product_ids=[])
    db.add(j)
    db.commit()
    db.refresh(j)
    got = jobsvc.get_job(db, j.id)
    check("get_job は既存行を返す", got is not None and got.id == j.id)
    check("get_job はジョブを起動しない（status は queued のまま）",
          got.status == DiscoveryJobStatus.queued.value)
    db.close()


def test_run_api_rejects_unavailable():
    print("test_run_api_rejects_unavailable")
    try:
        from fastapi.testclient import TestClient
        from app.main import app
    except ModuleNotFoundError as exc:
        print(f"  skip- API テストをスキップ（{exc}）")
        return
    client = TestClient(app)
    before = SessionLocal()
    n_before = before.query(DiscoveryJob).count()
    before.close()

    rb = client.post("/discovery/run", json={"source_platform": "backerkit", "limit": 5})
    check("BackerKit の run は 400（準備中）", rb.status_code == 400)
    rm = client.post("/discovery/run", json={"source_platform": "manual", "limit": 5})
    check("manual の run は 400（実取得非対応）", rm.status_code == 400)

    after = SessionLocal()
    n_after = after.query(DiscoveryJob).count()
    after.close()
    check("400 時はジョブを作らない", n_after == n_before)


def main():
    test_platform_registry()
    test_wadiz_adapter_dto()
    test_wadiz_adapter_null_preserved()
    test_zeczec_adapter_dto()
    test_zeczec_adapter_with_enrichment()
    test_zeczec_adapter_enrichment_none_ok()
    test_save_dedupe_and_no_project_write()
    test_no_empty_overwrite()
    test_zero_result_not_success()
    test_scoring_not_all_zero()
    test_scoring_missing_fields_not_zero()
    test_scoring_currency_not_confused()
    test_scoring_participants_as_backers()
    test_achievement_rate_normalized()
    test_job_completed_lifecycle()
    test_job_failed_keeps_error()
    test_job_dedupe_active()
    test_get_job_does_not_start()
    test_run_api_rejects_unavailable()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
