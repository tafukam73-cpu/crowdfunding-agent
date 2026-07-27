"""営業ステータス（sales_status）の変更履歴モデル。

案件（projects）の sales_status が遷移するたびに 1 行追加する追記専用テーブル。
「いつ・どの状態から・どの状態へ・どの経路で（手動 / 自動同期 など）変わったか」を
残し、パイプラインの進捗履歴として詳細画面のタイムラインに表示する。

方針：追記のみ（更新・削除しない）。案件削除時は CASCADE で一緒に消える。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StatusChangeSource(str, enum.Enum):
    """sales_status を変更した経路（誰が/何が変えたか）。"""

    manual = "manual"                    # 画面からの手動変更
    workflow = "workflow"                # 営業ワークフロー（準備完了判定など）
    copilot = "copilot"                  # Sales Copilot 由来
    gmail = "gmail"                      # メール送信（mark_sent）由来
    reply = "reply"                      # 返信登録（reply_confirm）由来
    followup = "followup"                # フォローアップ生成由来
    archive_restore = "archive_restore"  # アーカイブ / 復元に伴う変更
    system = "system"                    # その他システム自動


class ProjectStatusEvent(Base):
    __tablename__ = "project_status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 遷移前後の sales_status（正規化後の値を保存する）。新規作成時などは from_status=null。
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)

    # 変更経路（manual / workflow / copilot / gmail / reply / archive_restore ...）。
    change_source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=StatusChangeSource.manual.value,
        server_default=StatusChangeSource.manual.value,
    )
    # 任意の補足（自動同期の理由・操作メモ など）。
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
