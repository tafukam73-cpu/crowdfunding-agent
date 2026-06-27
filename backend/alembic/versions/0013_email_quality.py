"""email_drafts に件名候補・選択件名・トーン・日本語要約を追加

営業メール品質向上（件名 3 案・トーン選択・日本語要約）に対応。
既存の subject / body は後方互換のため残す。

Revision ID: 0013_email_quality
Revises: 0012_email_settings
Create Date: 2026-06-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_email_quality"
down_revision: Union[str, None] = "0012_email_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_drafts", sa.Column("subject_options", sa.JSON(), nullable=True)
    )
    op.add_column(
        "email_drafts", sa.Column("selected_subject", sa.Text(), nullable=True)
    )
    op.add_column(
        "email_drafts", sa.Column("tone", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "email_drafts", sa.Column("japanese_summary", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("email_drafts", "japanese_summary")
    op.drop_column("email_drafts", "tone")
    op.drop_column("email_drafts", "selected_subject")
    op.drop_column("email_drafts", "subject_options")
