"""日本未上陸判定: availability_checks / availability_hits、projects に最新判定

Revision ID: 0010_availability
Revises: 0009_email_provider
Create Date: 2026-06-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_availability"
down_revision: Union[str, None] = "0009_email_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "availability_checks",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(length=20), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("engine", sa.String(length=60), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_availability_checks_project_id", "availability_checks", ["project_id"])
    op.create_index("ix_availability_checks_verdict", "availability_checks", ["verdict"])

    op.create_table(
        "availability_hits",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("check_id", sa.Integer(), nullable=False),
        sa.Column("site", sa.String(length=20), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("match_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["check_id"], ["availability_checks.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_availability_hits_check_id", "availability_hits", ["check_id"])

    op.add_column("projects", sa.Column("latest_availability", sa.String(length=20), nullable=True))
    op.add_column("projects", sa.Column("latest_availability_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_projects_latest_availability", "projects", ["latest_availability"])


def downgrade() -> None:
    op.drop_index("ix_projects_latest_availability", table_name="projects")
    op.drop_column("projects", "latest_availability_at")
    op.drop_column("projects", "latest_availability")
    op.drop_index("ix_availability_hits_check_id", table_name="availability_hits")
    op.drop_table("availability_hits")
    op.drop_index("ix_availability_checks_verdict", table_name="availability_checks")
    op.drop_index("ix_availability_checks_project_id", table_name="availability_checks")
    op.drop_table("availability_checks")
