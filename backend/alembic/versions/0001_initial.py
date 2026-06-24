"""initial: projects テーブル作成

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("source_site", sa.String(length=30), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("video_url", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="USD"),
        sa.Column("goal_amount", sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column("raised_amount", sa.Numeric(precision=16, scale=2), nullable=True),
        sa.Column("backers_count", sa.BigInteger(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("maker_name", sa.String(length=255), nullable=True),
        sa.Column("maker_url", sa.Text(), nullable=True),
        sa.Column("contact_info", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="new"),
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
        sa.UniqueConstraint("source_url", name="uq_projects_source_url"),
    )
    op.create_index("ix_projects_source_site", "projects", ["source_site"])
    op.create_index("ix_projects_category", "projects", ["category"])
    op.create_index("ix_projects_status", "projects", ["status"])


def downgrade() -> None:
    op.drop_index("ix_projects_status", table_name="projects")
    op.drop_index("ix_projects_category", table_name="projects")
    op.drop_index("ix_projects_source_site", table_name="projects")
    op.drop_table("projects")
