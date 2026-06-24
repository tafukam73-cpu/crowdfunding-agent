"""usage_logs テーブル作成

Revision ID: 0005_usage_logs
Revises: 0004_email_drafts
Create Date: 2026-06-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_usage_logs"
down_revision: Union[str, None] = "0004_email_drafts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_logs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), nullable=False, server_default="0"),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_usage_logs_kind", "usage_logs", ["kind"])
    op.create_index("ix_usage_logs_project_id", "usage_logs", ["project_id"])
    op.create_index("ix_usage_logs_created_at", "usage_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_logs_created_at", table_name="usage_logs")
    op.drop_index("ix_usage_logs_project_id", table_name="usage_logs")
    op.drop_index("ix_usage_logs_kind", table_name="usage_logs")
    op.drop_table("usage_logs")
