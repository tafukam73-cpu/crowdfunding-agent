"""discovery_runs テーブルを追加（Discovery Engine v1-3：発掘実行ログ）

Discovery Crawler Framework の 1 回の実行を記録する。既存の discovered_products
には変更を加えない（v1-3 は収集の枠組みと実行ログのみ追加）。

Revision ID: 0036_discovery_runs
Revises: 0035_discovered_products
Create Date: 2026-07-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036_discovery_runs"
down_revision: Union[str, None] = "0035_discovered_products"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "discovery_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_platform", sa.String(length=30), nullable=False),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("found_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("saved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_discovery_runs_source_platform", "discovery_runs", ["source_platform"],
    )
    op.create_index("ix_discovery_runs_status", "discovery_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_discovery_runs_status", table_name="discovery_runs")
    op.drop_index("ix_discovery_runs_source_platform", table_name="discovery_runs")
    op.drop_table("discovery_runs")
