"""営業パイプライン：sales_status 変更履歴テーブルを追加

案件（projects）の sales_status 遷移履歴を残す追記専用テーブル
project_status_events を追加する。sales_status 自体は DB 上 String(20) 格納のため
enum 拡張（contract_agreed / import_prep / jp_cf_prep / selling / closed の追加）は
スキーマ変更を伴わない（コード側 enum の追加のみ）。

方針：追加のみ・非破壊。既存カラム・既存データには一切変更を加えない。
- 既存の projects.sales_status 値（won など）は書き換えない。読み取り時に
  normalize_sales_status() で won→contract_agreed に正規化する。

Revision ID: 0049_project_status_events
Revises: 0048_project_archive
Create Date: 2026-07-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0049_project_status_events"
down_revision: Union[str, None] = "0048_project_archive"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_status_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column(
            "change_source",
            sa.String(length=20),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_project_status_events_project_id",
        "project_status_events",
        ["project_id"],
    )
    op.create_index(
        "ix_project_status_events_created_at",
        "project_status_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_status_events_created_at", table_name="project_status_events"
    )
    op.drop_index(
        "ix_project_status_events_project_id", table_name="project_status_events"
    )
    op.drop_table("project_status_events")
