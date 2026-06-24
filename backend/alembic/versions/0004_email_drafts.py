"""email_drafts テーブル作成

Revision ID: 0004_email_drafts
Revises: 0003_ai_evaluations
Create Date: 2026-06-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_email_drafts"
down_revision: Union[str, None] = "0003_ai_evaluations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_drafts",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("email_type", sa.String(length=30), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False, server_default="en"),
        sa.Column("model", sa.String(length=60), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_email_drafts_project_id", "email_drafts", ["project_id"])
    op.create_index("ix_email_drafts_email_type", "email_drafts", ["email_type"])


def downgrade() -> None:
    op.drop_index("ix_email_drafts_email_type", table_name="email_drafts")
    op.drop_index("ix_email_drafts_project_id", table_name="email_drafts")
    op.drop_table("email_drafts")
