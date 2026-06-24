"""スクレイピング実行 API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

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
    started_at: datetime
    finished_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
