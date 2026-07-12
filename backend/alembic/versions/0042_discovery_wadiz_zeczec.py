"""Discovery に Wadiz/Zeczec/Ulule を接続する土台を追加

- discovered_products に currency（通貨）/ raw_data（発掘元生データ）列を追加する。
  KRW/TWD 等の金額を USD/JPY と誤解しないために通貨を保存し、source 固有フィールドの
  根拠を raw_data に残す（既存データ・列は変更しない。追加のみ）。
- discovery_jobs テーブルを新規作成する（発掘の非同期ジョブ。Contact Intelligence
  ジョブとは別テーブル・別同時実行枠）。

Revision ID: 0042_discovery_wz
Revises: 0041_wadiz_import
Create Date: 2026-07-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042_discovery_wz"
down_revision: Union[str, None] = "0041_wadiz_import"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- discovered_products：追加列（既存データは変更しない） ---
    op.add_column(
        "discovered_products",
        sa.Column("currency", sa.String(length=8), nullable=True),
    )
    op.add_column(
        "discovered_products",
        sa.Column("raw_data", sa.JSON(), nullable=True),
    )

    # --- discovery_jobs：発掘の非同期ジョブ ---
    op.create_table(
        "discovery_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_platform", sa.String(length=30), nullable=False),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("limit", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("auto_score", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_step", sa.String(length=160), nullable=True),
        sa.Column("found_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("saved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scored_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("product_ids", sa.JSON(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_discovery_jobs_source_platform", "discovery_jobs", ["source_platform"]
    )
    op.create_index("ix_discovery_jobs_status", "discovery_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_discovery_jobs_status", table_name="discovery_jobs")
    op.drop_index("ix_discovery_jobs_source_platform", table_name="discovery_jobs")
    op.drop_table("discovery_jobs")
    op.drop_column("discovered_products", "raw_data")
    op.drop_column("discovered_products", "currency")
