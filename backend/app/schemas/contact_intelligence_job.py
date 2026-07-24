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
    status: str            # queued/running/completed/failed/cancelled/timed_out
    progress: int
    current_step: str | None = None
    logs_json: list[CIJobLog] | None = None
    result_json: dict | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # 専用ワーカー実行のメタ情報（UI の「処理停止の可能性」判定・表示に使う）。
    worker_id: str | None = None
    heartbeat_at: datetime | None = None
    cancel_requested: bool = False

    # 適性ゲート不合格のまま管理者が手動実行したときの理由（通常実行は None）。
    gate_override_reason: str | None = None

    # キャッシュ再利用で返したかどうか（API が付与）。
    from_cache: bool = False

    model_config = ConfigDict(from_attributes=True)
