"""スクレイピング実行履歴モデル。

1 回の収集実行（サイト単位）を 1 レコードとして記録する。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ScrapeStatus(str, enum.Enum):
    running = "running"   # 実行中
    success = "success"   # 正常終了
    error = "error"       # エラー終了


class ErrorKind(str, enum.Enum):
    """エラーの種別（取得成功率監視・構造変化検知用）。

    - network   … 接続失敗・タイムアウト・403/429/5xx 等の一時的/取得系エラー
    - structure … 取得は成功したが期待する要素・キーが無い（構造変化の疑い）
    - unknown   … 上記に分類できないその他の例外
    """

    network = "network"
    structure = "structure"
    unknown = "unknown"


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    site: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ScrapeStatus.running.value, index=True
    )

    # どの収集ジョブ（job_runs）の一部か。手動/日次の親ジョブに紐づく（単発実行は null）
    job_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # 取得・反映件数
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # エラー種別（ErrorKind）。success 時は null。構造変化と一時障害の切り分けに使う。
    error_kind: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
