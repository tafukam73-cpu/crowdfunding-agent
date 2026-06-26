"""email_settings テーブル作成

差出人・会社情報・会社紹介文・署名テンプレートを保存する。
利用者は 1 人前提のためレコードは 1 件運用（id=1 を upsert）。

Revision ID: 0012_email_settings
Revises: 0011_scrape_error_kind
Create Date: 2026-06-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_email_settings"
down_revision: Union[str, None] = "0011_scrape_error_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_settings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("sender_name", sa.String(length=255), nullable=True),
        sa.Column("sender_title", sa.String(length=255), nullable=True),
        sa.Column("sender_department", sa.String(length=255), nullable=True),
        sa.Column("sender_email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=60), nullable=True),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("company_profile", sa.Text(), nullable=True),
        sa.Column("signature_template", sa.Text(), nullable=True),
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


def downgrade() -> None:
    op.drop_table("email_settings")
