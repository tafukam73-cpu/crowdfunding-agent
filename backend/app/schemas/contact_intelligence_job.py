"""Contact Intelligence 非同期ジョブ API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CIJobLog(BaseModel):
    ts: str | None = None
    message: str | None = None


class ContactIntelligenceJobOut(BaseModel):
    id: int
    project_id: int
    job_type: str
    status: str                       # queued/running/completed/failed/cancelled
    progress: int
    current_step: str | None = None
    logs_json: list[CIJobLog] | None = None
    result_json: dict | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # キャッシュ再利用で返したかどうか（API が付与）。
    from_cache: bool = False

    model_config = ConfigDict(from_attributes=True)
