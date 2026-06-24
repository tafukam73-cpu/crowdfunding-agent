"""scrape_runs テーブル作成

Revision ID: 0002_scrape_runs
Revises: 0001_initial
Create Date: 2026-06-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_scrape_runs"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scrape_runs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("site", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("fetched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_scrape_runs_site", "scrape_runs", ["site"])
    op.create_index("ix_scrape_runs_status", "scrape_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_scrape_runs_status", table_name="scrape_runs")
    op.drop_index("ix_scrape_runs_site", table_name="scrape_runs")
    op.drop_table("scrape_runs")
