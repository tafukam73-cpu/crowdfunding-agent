"""ai_evaluations テーブル作成＋projects に評価キャッシュ列追加

Revision ID: 0003_ai_evaluations
Revises: 0002_scrape_runs
Create Date: 2026-06-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_ai_evaluations"
down_revision: Union[str, None] = "0002_scrape_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # projects に最新評価キャッシュ列
    op.add_column("projects", sa.Column("latest_score", sa.Integer(), nullable=True))
    op.add_column(
        "projects", sa.Column("latest_recommendation", sa.String(length=10), nullable=True)
    )
    op.create_index("ix_projects_latest_score", "projects", ["latest_score"])
    op.create_index(
        "ix_projects_latest_recommendation", "projects", ["latest_recommendation"]
    )

    # ai_evaluations
    op.create_table(
        "ai_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=False),
        sa.Column("recommendation", sa.String(length=10), nullable=False),
        sa.Column("axis_scores", sa.JSON(), nullable=False),
        sa.Column("reasons", sa.Text(), nullable=True),
        sa.Column("concerns", sa.Text(), nullable=True),
        sa.Column("sales_comment", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=60), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_ai_evaluations_project_id", "ai_evaluations", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_evaluations_project_id", table_name="ai_evaluations")
    op.drop_table("ai_evaluations")
    op.drop_index("ix_projects_latest_recommendation", table_name="projects")
    op.drop_index("ix_projects_latest_score", table_name="projects")
    op.drop_column("projects", "latest_recommendation")
    op.drop_column("projects", "latest_score")
