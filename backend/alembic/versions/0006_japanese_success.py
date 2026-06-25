"""japanese_success_projects テーブル作成

日本クラファン（Makuake / GreenFunding）の成功案件を比較用に保存する。
海外案件（projects）とは別管理。

Revision ID: 0006_japanese_success
Revises: 0005_usage_logs
Create Date: 2026-06-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_japanese_success"
down_revision: Union[str, None] = "0005_usage_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "japanese_success_projects",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("video_url", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="JPY"),
        sa.Column("goal_amount", sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column("raised_amount", sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column("backers_count", sa.BigInteger(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("maker_name", sa.String(length=255), nullable=True),
        sa.Column("maker_url", sa.Text(), nullable=True),
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
    )
    op.create_index(
        "ix_japanese_success_projects_platform",
        "japanese_success_projects",
        ["platform"],
    )
    op.create_index(
        "ix_japanese_success_projects_category",
        "japanese_success_projects",
        ["category"],
    )
    # source_url は一意（NULL は複数可）。upsert のキーに使う。
    op.create_index(
        "ix_japanese_success_projects_source_url",
        "japanese_success_projects",
        ["source_url"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_japanese_success_projects_source_url",
        table_name="japanese_success_projects",
    )
    op.drop_index(
        "ix_japanese_success_projects_category",
        table_name="japanese_success_projects",
    )
    op.drop_index(
        "ix_japanese_success_projects_platform",
        table_name="japanese_success_projects",
    )
    op.drop_table("japanese_success_projects")
