"""email_drafts にパーソナライズ材料を追加

営業メールの個別化（personalization_context / personalized_compliment /
product_highlights）に対応。既存の生成・表示は壊さない（すべて任意カラム）。

Revision ID: 0014_email_personalization
Revises: 0013_email_quality
Create Date: 2026-06-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_email_personalization"
down_revision: Union[str, None] = "0013_email_quality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "email_drafts",
        sa.Column("personalization_context", sa.JSON(), nullable=True),
    )
    op.add_column(
        "email_drafts",
        sa.Column("personalized_compliment", sa.Text(), nullable=True),
    )
    op.add_column(
        "email_drafts",
        sa.Column("product_highlights", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("email_drafts", "product_highlights")
    op.drop_column("email_drafts", "personalized_compliment")
    op.drop_column("email_drafts", "personalization_context")
