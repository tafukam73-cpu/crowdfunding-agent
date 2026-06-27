"""contact_discoveries テーブル作成

公式サイト・問い合わせページ・SNS から営業先連絡先を探索した結果を保存する。

Revision ID: 0016_contact_discovery
Revises: 0015_company_research
Create Date: 2026-06-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_contact_discovery"
down_revision: Union[str, None] = "0015_company_research"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contact_discoveries",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("maker_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("primary_email", sa.Text(), nullable=True),
        sa.Column("primary_contact_form_url", sa.Text(), nullable=True),
        sa.Column("official_site_url", sa.Text(), nullable=True),
        sa.Column("instagram_url", sa.Text(), nullable=True),
        sa.Column("facebook_url", sa.Text(), nullable=True),
        sa.Column("twitter_url", sa.Text(), nullable=True),
        sa.Column("linkedin_url", sa.Text(), nullable=True),
        sa.Column("youtube_url", sa.Text(), nullable=True),
        sa.Column("discovered_emails", sa.JSON(), nullable=True),
        sa.Column("discovered_forms", sa.JSON(), nullable=True),
        sa.Column("discovered_socials", sa.JSON(), nullable=True),
        sa.Column("searched_urls", sa.JSON(), nullable=True),
        sa.Column("confidence_score", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_contact_discoveries_project_id", "contact_discoveries", ["project_id"]
    )
    op.create_index(
        "ix_contact_discoveries_maker_id", "contact_discoveries", ["maker_id"]
    )
    op.create_index(
        "ix_contact_discoveries_status", "contact_discoveries", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_contact_discoveries_status", table_name="contact_discoveries")
    op.drop_index("ix_contact_discoveries_maker_id", table_name="contact_discoveries")
    op.drop_index(
        "ix_contact_discoveries_project_id", table_name="contact_discoveries"
    )
    op.drop_table("contact_discoveries")
