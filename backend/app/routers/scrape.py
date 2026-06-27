"""スクレイピング手動実行・履歴 API。

- POST /scrape/run        手動収集（サイト指定可。未指定で全サイト）
- GET  /scrape/runs       実行履歴
- GET  /scrape/runs/{id}  実行詳細
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.models.job_run import JobRun
from app.models.project import JAPANESE_SUCCESS_SITES, SourceSite
from app.models.scrape_run import ScrapeRun
from app.scrapers.registry import SUPPORTED_SITES
from app.schemas.scrape import (
    JobRunOut,
    ScheduleStatusOut,
    ScrapeRunOut,
    ScrapeRunRequest,
    ScrapeStatsOut,
    SiteLastRun,
    SiteStatsOut,
)
from app.services import (
    alert_service,
    collection_job,
    collector,
    scheduler,
    scrape_monitor,
)

router = APIRouter(prefix="/scrape", tags=["scrape"])

# ダッシュボードに最終実行結果を表示する対象サイト（日次収集の対象）
DASHBOARD_SITES = [
    SourceSite.kickstarter,
    SourceSite.indiegogo,
    SourceSite.makuake,
    SourceSite.greenfunding,
]


@router.post("/run", response_model=list[ScrapeRunOut], status_code=status.HTTP_202_ACCEPTED)
def run_scrape(
    background_tasks: BackgroundTasks,
    payload: ScrapeRunRequest | None = None,
    db: Session = Depends(get_db),
) -> list[ScrapeRun]:
    """収集をバックグラウンド実行で開始し、即座に running な実行レコードを返す。

    実際の収集はレスポンス返却後に背景で進行する。進捗・結果は
    GET /scrape/runs で確認する（status: running → success / error）。
    """
    payload = payload or ScrapeRunRequest()
    if payload.site in JAPANESE_SUCCESS_SITES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{payload.site.value} は営業対象外です。日本の成功事例は "
                "POST /japanese-success/collect で収集してください。"
            ),
        )
    sites = [payload.site] if payload.site else list(SUPPORTED_SITES)

    runs = collector.create_pending_runs(db, sites)
    run_ids = [r.id for r in runs]
    background_tasks.add_task(collector.run_pending, run_ids, payload.limit)
    return runs


@router.post("/run-all", status_code=status.HTTP_202_ACCEPTED)
def run_all(background_tasks: BackgroundTasks) -> dict:
    """4 サイト（KS / Indiegogo / Makuake / GreenFunding）を一括でバックグラウンド収集。

    日次スケジューラと同一のジョブ（collection_job）を手動起動する。二重実行は
    ロックで防止され、結果は job_runs / scrape_runs に記録される。
    """
    background_tasks.add_task(collection_job.run_collection, "manual")
    return {"status": "started"}


@router.get("/last", response_model=ScheduleStatusOut)
def schedule_status(db: Session = Depends(get_db)) -> ScheduleStatusOut:
    """スケジューラ状態・最新ジョブ・サイト別の最終実行結果を返す。"""
    sites: list[SiteLastRun] = []
    for site in DASHBOARD_SITES:
        last = db.scalar(
            select(ScrapeRun)
            .where(ScrapeRun.site == site.value)
            .order_by(desc(ScrapeRun.started_at), desc(ScrapeRun.id))
            .limit(1)
        )
        sites.append(SiteLastRun(site=site, last_run=last))

    last_job = db.scalar(
        select(JobRun).order_by(desc(JobRun.started_at), desc(JobRun.id)).limit(1)
    )

    return ScheduleStatusOut(
        enabled=settings.scrape_schedule_enabled,
        cron=settings.scrape_schedule_cron,
        timezone=settings.scrape_timezone,
        next_run_time=scheduler.next_run_time(),
        last_job=last_job,
        sites=sites,
    )


@router.get("/stats", response_model=ScrapeStatsOut)
def scrape_stats(
    db: Session = Depends(get_db),
    window: int = Query(20, ge=1, le=200),
) -> ScrapeStatsOut:
    """サイト別の取得成功率・エラー種別内訳（直近 window 件）を返す。

    構造変化検知（error_kind=structure）があれば structure_change_suspected が
    true になり、セレクタ/API 仕様の見直しが必要なサインとして使える。
    """
    rep = scrape_monitor.report(db, window=window)
    return ScrapeStatsOut(
        window=rep.window,
        threshold=rep.threshold,
        structure_change_suspected=rep.structure_change_suspected,
        degraded=rep.degraded,
        sites=[
            SiteStatsOut(
                site=s.site,
                window=s.window,
                total=s.total,
                success=s.success,
                errors=s.errors,
                network_errors=s.network_errors,
                structure_errors=s.structure_errors,
                unknown_errors=s.unknown_errors,
                http_403_count=s.http_403_count,
                success_rate=s.success_rate,
                last_status=s.last_status,
                last_run_at=s.last_run_at,
                last_success_at=s.last_success_at,
                last_failure_at=s.last_failure_at,
                structure_change_suspected=s.structure_change_suspected,
                last_structure_error_at=s.last_structure_error_at,
                degraded=s.degraded,
            )
            for s in rep.sites
        ],
    )


@router.post("/alert-test")
def alert_test() -> dict:
    """設定済みの通知先（Slack 等）へテストアラートを送る（疎通確認用）。

    通知先未設定なら {"notified": false, "reason": "no notifier configured"} を返す。
    """
    return alert_service.send_test()


@router.get("/jobs", response_model=list[JobRunOut])
def list_jobs(
    db: Session = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
) -> list[JobRun]:
    """収集ジョブの実行履歴（新しい順）。"""
    stmt = select(JobRun).order_by(desc(JobRun.started_at), desc(JobRun.id)).limit(limit)
    return list(db.scalars(stmt))


@router.get("/runs", response_model=list[ScrapeRunOut])
def list_runs(
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
) -> list[ScrapeRun]:
    stmt = select(ScrapeRun).order_by(desc(ScrapeRun.started_at)).limit(limit)
    return list(db.scalars(stmt))


@router.get("/runs/{run_id}", response_model=ScrapeRunOut)
def get_run(run_id: int, db: Session = Depends(get_db)) -> ScrapeRun:
    run = db.get(ScrapeRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="実行履歴が見つかりません")
    return run
