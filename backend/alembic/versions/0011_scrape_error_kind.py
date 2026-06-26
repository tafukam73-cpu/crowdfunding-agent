"""scrape_runs にエラー種別カラム追加

取得成功率監視・構造変化検知のため、失敗を network / structure / unknown に
分類する error_kind 列を追加する。

Revision ID: 0011_scrape_error_kind
Revises: 0010_availability
Create Date: 2026-06-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_scrape_error_kind"
down_revision: Union[str, None] = "0010_availability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scrape_runs",
        sa.Column("error_kind", sa.String(length=20), nullable=True),
    )
    op.create_index(
        "ix_scrape_runs_error_kind", "scrape_runs", ["error_kind"]
    )


def downgrade() -> None:
    op.drop_index("ix_scrape_runs_error_kind", table_name="scrape_runs")
    op.drop_column("scrape_runs", "error_kind")
