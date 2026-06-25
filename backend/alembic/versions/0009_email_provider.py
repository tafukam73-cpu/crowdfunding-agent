"""email_drafts に provider / provider_draft_id を追加

メール下書きをプロバイダー（Gmail 等）に作成した記録。

Revision ID: 0009_email_provider
Revises: 0008_crm
Create Date: 2026-06-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_email_provider"
down_revision: Union[str, None] = "0008_crm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("email_drafts", sa.Column("provider", sa.String(length=20), nullable=True))
    op.add_column("email_drafts", sa.Column("provider_draft_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("email_drafts", "provider_draft_id")
    op.drop_column("email_drafts", "provider")
