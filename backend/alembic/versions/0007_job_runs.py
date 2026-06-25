"""job_runs / job_locks 作成、scrape_runs に job_run_id 追加

収集ジョブ（手動/日次共通）の実行履歴と二重実行防止ロック。

Revision ID: 0007_job_runs
Revises: 0006_japanese_success
Create Date: 2026-06-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_job_runs"
down_revision: Union[str, None] = "0006_japanese_success"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("sites_succeeded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sites_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_job_runs_trigger", "job_runs", ["trigger"])
    op.create_index("ix_job_runs_status", "job_runs", ["status"])

    op.create_table(
        "job_locks",
        sa.Column("name", sa.String(length=50), primary_key=True, nullable=False),
        sa.Column("job_run_id", sa.Integer(), nullable=True),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.add_column(
        "scrape_runs", sa.Column("job_run_id", sa.Integer(), nullable=True)
    )
    op.create_index("ix_scrape_runs_job_run_id", "scrape_runs", ["job_run_id"])


def downgrade() -> None:
    op.drop_index("ix_scrape_runs_job_run_id", table_name="scrape_runs")
    op.drop_column("scrape_runs", "job_run_id")
    op.drop_table("job_locks")
    op.drop_index("ix_job_runs_status", table_name="job_runs")
    op.drop_index("ix_job_runs_trigger", table_name="job_runs")
    op.drop_table("job_runs")
