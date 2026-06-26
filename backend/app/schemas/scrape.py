"""スクレイピング実行 API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.job_run import JobStatus, JobTrigger
from app.models.project import SourceSite
from app.models.scrape_run import ScrapeStatus


class ScrapeRunRequest(BaseModel):
    """手動実行リクエスト。

    site 未指定なら対応全サイトを順に収集する。
    """

    site: SourceSite | None = None
    limit: int = 20


class ScrapeRunOut(BaseModel):
    id: int
    site: SourceSite
    status: ScrapeStatus
    fetched_count: int
    created_count: int
    updated_count: int
    error: str | None
    error_kind: str | None = None
    started_at: datetime
    finished_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class SiteLastRun(BaseModel):
    """サイトごとの最新実行結果（未実行なら last_run=None）。"""

    site: SourceSite
    last_run: ScrapeRunOut | None = None


class JobRunOut(BaseModel):
    """収集ジョブ（手動/日次）1 回分の実行履歴。"""

    id: int
    trigger: JobTrigger
    status: JobStatus
    sites_succeeded: int
    sites_failed: int
    error: str | None
    started_at: datetime
    finished_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class ScheduleStatusOut(BaseModel):
    """日次スケジューラの状態・最新ジョブ・サイト別の最終実行結果。"""

    enabled: bool
    cron: str
    timezone: str
    next_run_time: datetime | None
    last_job: JobRunOut | None
    sites: list[SiteLastRun]


class SiteStatsOut(BaseModel):
    """サイト別の取得成功率・エラー種別内訳（直近 window 件）。"""

    site: SourceSite
    window: int
    total: int
    success: int
    errors: int
    network_errors: int
    structure_errors: int
    unknown_errors: int
    success_rate: float | None
    last_status: str | None
    last_run_at: datetime | None
    structure_change_suspected: bool
    last_structure_error_at: datetime | None
    degraded: bool


class ScrapeStatsOut(BaseModel):
    """取得監視レポート（全サイト集計）。

    - structure_change_suspected: いずれかのサイトで構造変化を検知（要対応）
    - degraded: いずれかのサイトで成功率がしきい値を割っている
    """

    window: int
    threshold: float
    structure_change_suspected: bool
    degraded: bool
    sites: list[SiteStatsOut]
