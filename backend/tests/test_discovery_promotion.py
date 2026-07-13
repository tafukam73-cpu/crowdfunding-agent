"""Discovery → Projects 昇格ワークフローのオフライン検証（実ネットワーク不要）。

要件 10 の最低限テストを pytest 非依存で単体実行できる形で検証する。

実行（backend ディレクトリで）:
    python tests/test_discovery_promotion.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "discovery_promotion_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.models.contact_intelligence_job import ContactIntelligenceJob  # noqa: E402
from app.models.discovered_product import (  # noqa: E402
    DiscoveredProduct,
    DiscoveryPromotionStatus,
)
from app.models.project import Project, SourceSite  # noqa: E402
from app.models.sales_assessment import SalesAssessment  # noqa: E402
from app.schemas.project import ProjectCreate  # noqa: E402
from app.services import (  # noqa: E402
    discovery_promotion_service as promo,
    discovery_service as dsvc,
    project_service,
    sales_copilot_service,
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


# Contact Intelligence ジョブは背景スレッドを起こさず、行だけ作る（no-op runner）。
_ci_runs: list[int] = []


def _noop_ci_runner(job_id: int) -> None:
    _ci_runs.append(job_id)


def _mk_product(db, **kwargs):
    product, _ = dsvc.create(db, kwargs)
    return product


def _count_projects(db) -> int:
    return db.query(Project).count()


def _promote(db, product_id):
    return promo.promote(db, product_id, contact_runner=_noop_ci_runner)


def test_preview_no_db_change():
    print("test_preview_no_db_change")
    db = SessionLocal()
    p = _mk_product(
        db, source_platform="kickstarter",
        source_url="https://www.kickstarter.com/projects/x/preview-widget",
        product_name="Preview Widget", creator_name="X Maker",
        category="kitchen", funding_amount=1000, funding_goal=500, currency="USD",
        backers_count=120,
    )
    before_projects = _count_projects(db)
    before_status = p.promotion_status
    prev = promo.build_preview(db, p)
    after_projects = _count_projects(db)
    db.refresh(p)
    check("preview は projects を作らない", before_projects == after_projects)
    check("preview は promotion_status を変えない", p.promotion_status == before_status)
    check("preview の target_fields に title が入る",
          prev["target_fields"]["title"] == "Preview Widget")
    check("preview の推奨は promote", prev["recommended_action"] == "promote")
    check("達成率が算出される（1000/500=200%）",
          prev["target_fields"]["achievement_rate"] == 200)
    db.close()


def test_promote_creates_project():
    print("test_promote_creates_project")
    db = SessionLocal()
    p = _mk_product(
        db, source_platform="wadiz",
        source_url="https://www.wadiz.kr/web/campaign/detail/promo1",
        product_name="Promo Gadget", creator_name="Promo Maker",
        category="gadget", funding_amount=5000, funding_goal=1000,
        currency="KRW", backers_count=300,
        official_website_url="https://promo-maker.example",
    )
    before = _count_projects(db)
    res = _promote(db, p.id)
    after = _count_projects(db)
    check("status=promoted", res["status"] == "promoted")
    check("projects が 1 件増える", after == before + 1)
    check("project_id が返る", res["project_id"] is not None)
    project = db.get(Project, res["project_id"])
    check("title が引き継がれる", project.title == "Promo Gadget")
    check("source_site が wadiz にマップされる",
          project.source_site == SourceSite.wadiz.value)
    check("currency（KRW）が保持される", project.currency == "KRW")
    check("raised_amount が引き継がれる", float(project.raised_amount) == 5000)
    check("maker_url は公式サイトを使う（推測しない）",
          project.maker_url == "https://promo-maker.example")
    check("enrichment に source_discovered_product_id が残る",
          (project.enrichment or {}).get("source_discovered_product_id") == p.id)
    # promotion_status / promoted_project_id 更新
    db.refresh(p)
    check("promotion_status=promoted",
          p.promotion_status == DiscoveryPromotionStatus.promoted.value)
    check("promoted_project_id 保持", p.promoted_project_id == project.id)
    check("promoted_at 設定", p.promoted_at is not None)
    # Sales Assessment 作成
    sa = db.query(SalesAssessment).filter(
        SalesAssessment.project_id == project.id
    ).count()
    check("Sales Assessment が作成される", sa >= 1)
    check("assessment_status=created", res["assessment_status"] == "created")
    # Contact Intelligence job 作成（queued）
    check("contact_job_id が返る", res["contact_job_id"] is not None)
    job = db.get(ContactIntelligenceJob, res["contact_job_id"])
    check("Contact Intelligence job 行が作られる", job is not None)
    check("job は queued", job.status == "queued")
    check("job は project にひもづく", job.project_id == project.id)
    # Sales Copilot 表示（カードが作れる）
    card = sales_copilot_service.project_copilot(db, project)
    check("Sales Copilot カードが生成される", card["project_id"] == project.id)
    db.close()


def test_no_guessed_values():
    print("test_no_guessed_values")
    db = SessionLocal()
    # 公式サイト無し・メーカー名無し → maker_url / maker_name は null（推測しない）
    p = _mk_product(
        db, source_platform="indiegogo",
        source_url="https://www.indiegogo.com/projects/noguess-thing",
        product_name="NoGuess Thing",
    )
    res = _promote(db, p.id)
    project = db.get(Project, res["project_id"])
    check("メーカー名は推測せず null", project.maker_name is None)
    check("maker_url は推測せず null", project.maker_url is None)
    db.close()


def test_source_url_duplicate():
    print("test_source_url_duplicate")
    db = SessionLocal()
    url = "https://www.kickstarter.com/projects/dup/exact-widget"
    # 既存 project を作る
    existing = project_service.create_project(db, ProjectCreate(
        title="Exact Widget", source_site=SourceSite.kickstarter, source_url=url,
    ))
    p = _mk_product(
        db, source_platform="kickstarter", source_url=url,
        product_name="Exact Widget Dup",
    )
    before = _count_projects(db)
    res = _promote(db, p.id)
    after = _count_projects(db)
    check("status=duplicate_project", res["status"] == "duplicate_project")
    check("重複時は projects を増やさない", after == before)
    check("既存 project_id を返す", res["project_id"] == existing.id)
    check("一致種別 source_url", res["duplicate_match_kind"] == "source_url")
    db.refresh(p)
    check("promotion_status=duplicate_project",
          p.promotion_status == DiscoveryPromotionStatus.duplicate_project.value)
    check("promoted_project_id=既存", p.promoted_project_id == existing.id)
    db.close()


def test_platform_source_id_duplicate():
    print("test_platform_source_id_duplicate")
    db = SessionLocal()
    # 既存 project（クエリ付き URL）と、slug だけ同じ product（クエリ無し・www 無し）
    existing = project_service.create_project(db, ProjectCreate(
        title="Slug Widget", source_site=SourceSite.kickstarter,
        source_url="https://www.kickstarter.com/projects/creatorx/slug-widget?ref=abc",
    ))
    p = _mk_product(
        db, source_platform="kickstarter",
        source_url="https://kickstarter.com/projects/creatorx/slug-widget",
        product_name="Slug Widget New",
    )
    before = _count_projects(db)
    res = _promote(db, p.id)
    after = _count_projects(db)
    check("status=duplicate_project（platform+source_id）",
          res["status"] == "duplicate_project")
    check("projects を増やさない", after == before)
    check("既存 project_id を返す", res["project_id"] == existing.id)
    check("一致種別 platform_source_id",
          res["duplicate_match_kind"] == "platform_source_id")
    db.close()


def test_approximate_is_warning_only():
    print("test_approximate_is_warning_only")
    db = SessionLocal()
    # タイトルは同じだが URL（slug）は別 → 近似一致は警告のみ・新規作成する
    project_service.create_project(db, ProjectCreate(
        title="Similar Lamp", source_site=SourceSite.zeczec,
        source_url="https://www.zeczec.com/projects/lamp-a",
        maker_name="Lamp Co",
    ))
    p = _mk_product(
        db, source_platform="zeczec",
        source_url="https://www.zeczec.com/projects/lamp-b",
        product_name="Similar Lamp", creator_name="Lamp Co",
    )
    prev = promo.build_preview(db, p)
    check("重複判定は None（近似一致は重複にしない）",
          prev["duplicate_project"] is None)
    check("近似一致が候補として提示される",
          len(prev["approximate_matches"]) >= 1)
    before = _count_projects(db)
    res = _promote(db, p.id)
    after = _count_projects(db)
    check("近似一致でも自動統合せず新規作成する", res["status"] == "promoted")
    check("projects が 1 件増える", after == before + 1)
    db.close()


def test_double_promote_no_duplicate():
    print("test_double_promote_no_duplicate")
    db = SessionLocal()
    p = _mk_product(
        db, source_platform="ulule",
        source_url="https://www.ulule.com/double-promote/",
        product_name="Double Promote",
    )
    first = _promote(db, p.id)
    before = _count_projects(db)
    second = _promote(db, p.id)
    after = _count_projects(db)
    check("2 回目は promoted のまま", second["status"] == "promoted")
    check("2 回目は already_promoted", second["already_promoted"] is True)
    check("2 回目は projects を増やさない", after == before)
    check("2 回目も同じ project_id", second["project_id"] == first["project_id"])
    db.close()


def test_batch_partial_failure_continues():
    print("test_batch_partial_failure_continues")
    db = SessionLocal()
    ok = _mk_product(
        db, source_platform="kickstarter",
        source_url="https://www.kickstarter.com/projects/batch/ok-1",
        product_name="Batch OK",
    )
    # 既存 project と重複する product
    project_service.create_project(db, ProjectCreate(
        title="Batch Dup", source_site=SourceSite.kickstarter,
        source_url="https://www.kickstarter.com/projects/batch/dup-1",
    ))
    dup = _mk_product(
        db, source_platform="kickstarter",
        source_url="https://www.kickstarter.com/projects/batch/dup-1",
        product_name="Batch Dup New",
    )
    missing_id = 99999999  # 存在しない
    before = _count_projects(db)
    out = promo.promote_batch(
        db, [ok.id, dup.id, missing_id, ok.id],  # ok.id 重複は 1 回だけ処理
        contact_runner=_noop_ci_runner,
    )
    after = _count_projects(db)
    check("重複 ID は 1 回だけ処理", out["processed"] == 3)
    check("成功 1 件", out["counts"]["promoted"] == 1)
    check("重複 1 件", out["counts"]["duplicate_project"] == 1)
    check("不存在 1 件", out["counts"]["not_found"] == 1)
    check("成功で projects が 1 件だけ増える", after == before + 1)
    check("一部失敗でも他を継続（結果が 3 件）", len(out["results"]) == 3)
    db.close()


def test_api_performance():
    print("test_api_performance")
    db = SessionLocal()
    p = _mk_product(
        db, source_platform="kickstarter",
        source_url="https://www.kickstarter.com/projects/perf/fast-widget",
        product_name="Fast Widget", creator_name="Perf Maker",
        funding_amount=2000, funding_goal=1000,
    )
    t0 = time.time()
    _promote(db, p.id)
    elapsed = time.time() - t0
    check(f"promote は 3 秒以内（{elapsed:.3f}s）", elapsed < 3.0)
    db.close()


def test_api_endpoint():
    print("test_api_endpoint")
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        import app.services.contact_intelligence_service as ci
    except ModuleNotFoundError as exc:
        print(f"  skip- API テストをスキップ（{exc}）")
        return

    # create_job のスレッド起動を no-op に差し替え（テスト中のみ）。
    original = ci.create_job

    def _patched_create_job(db, project, job_type, *, force=False, runner=None):
        return original(db, project, job_type, force=force, runner=_noop_ci_runner)

    ci.create_job = _patched_create_job
    try:
        client = TestClient(app)
        r = client.post("/discovery/products", json={
            "source_platform": "kickstarter",
            "source_url": "https://www.kickstarter.com/projects/api/promote-widget",
            "product_name": "API Promote Widget",
            "creator_name": "API Maker",
            "funding_amount": 3000, "funding_goal": 1000,
        })
        check("商品作成 200", r.status_code == 200)
        pid = r.json()["id"]

        # preview は DB を更新しない
        rp = client.post(f"/discovery/products/{pid}/promote/preview")
        check("preview 200", rp.status_code == 200)
        check("preview recommended=promote",
              rp.json()["recommended_action"] == "promote")
        r_get = client.get(f"/discovery/products/{pid}")
        check("preview 後も not_promoted（GET で昇格しない）",
              r_get.json()["promotion_status"] == "not_promoted")

        # promote
        r2 = client.post(f"/discovery/products/{pid}/promote")
        check("promote 200", r2.status_code == 200)
        body = r2.json()
        check("promote status=promoted", body["status"] == "promoted")
        check("project_id が返る", body["project_id"] is not None)
        check("assessment_status=created", body["assessment_status"] == "created")
        check("contact_job_id が返る", body["contact_job_id"] is not None)

        # 商品に promoted が反映
        r3 = client.get(f"/discovery/products/{pid}")
        check("promotion_status=promoted",
              r3.json()["promotion_status"] == "promoted")
        check("promoted_project_id 反映",
              r3.json()["promoted_project_id"] == body["project_id"])

        # 2 回目は新規作成しない
        r4 = client.post(f"/discovery/products/{pid}/promote")
        check("2 回目 already_promoted", r4.json()["already_promoted"] is True)
        check("2 回目 同じ project_id",
              r4.json()["project_id"] == body["project_id"])

        # batch
        r5 = client.post("/discovery/products", json={
            "source_platform": "kickstarter",
            "source_url": "https://www.kickstarter.com/projects/api/batch-widget",
            "product_name": "API Batch Widget",
        })
        pid2 = r5.json()["id"]
        r6 = client.post("/discovery/products/promote-batch",
                         json={"product_ids": [pid2, 99999999]})
        check("batch 200", r6.status_code == 200)
        check("batch 成功 1 件", r6.json()["counts"]["promoted"] == 1)
        check("batch 不存在 1 件", r6.json()["counts"]["not_found"] == 1)

        # 存在しない商品の promote は 404
        r7 = client.post("/discovery/products/99999999/promote")
        check("存在しない商品は 404", r7.status_code == 404)
    finally:
        ci.create_job = original


def main():
    test_preview_no_db_change()
    test_promote_creates_project()
    test_no_guessed_values()
    test_source_url_duplicate()
    test_platform_source_id_duplicate()
    test_approximate_is_warning_only()
    test_double_promote_no_duplicate()
    test_batch_partial_failure_continues()
    test_api_performance()
    test_api_endpoint()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
