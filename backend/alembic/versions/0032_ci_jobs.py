"""contact_intelligence_jobs テーブルを追加

Contact Intelligence の重い探索（Web調査 / Document Reader / Search Agent /
full）を非同期ジョブ化し、進捗・ログ・結果を保存する。

Revision ID: 0032_ci_jobs
Revises: 0031_search_diag
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_ci_jobs"
down_revision: Union[str, None] = "0031_search_diag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contact_intelligence_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id", sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("job_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_step", sa.String(length=120), nullable=True),
        sa.Column("logs_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ci_jobs_project_id", "contact_intelligence_jobs", ["project_id"])
    op.create_index("ix_ci_jobs_job_type", "contact_intelligence_jobs", ["job_type"])
    op.create_index("ix_ci_jobs_status", "contact_intelligence_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_ci_jobs_status", table_name="contact_intelligence_jobs")
    op.drop_index("ix_ci_jobs_job_type", table_name="contact_intelligence_jobs")
    op.drop_index("ix_ci_jobs_project_id", table_name="contact_intelligence_jobs")
    op.drop_table("contact_intelligence_jobs")
