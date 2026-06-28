"""japan_sales_checks テーブル作成

営業前に「既に日本で販売されていないか」を AI が調査し、営業価値（★1〜5）を
判定した結果を保存する。

Revision ID: 0021_japan_sales_check
Revises: 0020_project_description_clean
Create Date: 2026-06-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_japan_sales_check"
down_revision: Union[str, None] = "0020_project_description_clean"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "japan_sales_checks",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("maker_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("sales_value_stars", sa.Integer(), nullable=True),
        sa.Column("channels", sa.JSON(), nullable=True),
        sa.Column("search_queries", sa.JSON(), nullable=True),
        sa.Column("ai_comment", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_japan_sales_checks_project_id", "japan_sales_checks", ["project_id"]
    )
    op.create_index(
        "ix_japan_sales_checks_maker_id", "japan_sales_checks", ["maker_id"]
    )
    op.create_index(
        "ix_japan_sales_checks_status", "japan_sales_checks", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_japan_sales_checks_status", table_name="japan_sales_checks")
    op.drop_index("ix_japan_sales_checks_maker_id", table_name="japan_sales_checks")
    op.drop_index("ix_japan_sales_checks_project_id", table_name="japan_sales_checks")
    op.drop_table("japan_sales_checks")
