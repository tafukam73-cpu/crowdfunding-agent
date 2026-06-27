"""company_researches テーブル作成

AI 企業リサーチ（メーカー・商品の追加調査）結果を保存する。
営業メール生成の前段で利用し、より相手企業ごとに個別化したメールを作る。

Revision ID: 0015_company_research
Revises: 0014_email_personalization
Create Date: 2026-06-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_company_research"
down_revision: Union[str, None] = "0014_email_personalization"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_researches",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("maker_name", sa.Text(), nullable=True),
        sa.Column("official_site_url", sa.Text(), nullable=True),
        sa.Column("project_url", sa.Text(), nullable=True),
        sa.Column(
            "research_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("brand_summary", sa.Text(), nullable=True),
        sa.Column("company_mission", sa.Text(), nullable=True),
        sa.Column("product_summary", sa.Text(), nullable=True),
        sa.Column("key_product_features", sa.JSON(), nullable=True),
        sa.Column("brand_strengths", sa.JSON(), nullable=True),
        sa.Column("differentiation_points", sa.JSON(), nullable=True),
        sa.Column("japan_market_fit", sa.Text(), nullable=True),
        sa.Column("personalized_compliment", sa.Text(), nullable=True),
        sa.Column("outreach_angles", sa.JSON(), nullable=True),
        sa.Column("risks_or_cautions", sa.JSON(), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(length=60), nullable=True),
        sa.Column("raw_notes", sa.Text(), nullable=True),
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
        "ix_company_researches_project_id", "company_researches", ["project_id"]
    )
    op.create_index(
        "ix_company_researches_research_status",
        "company_researches",
        ["research_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_company_researches_research_status", table_name="company_researches"
    )
    op.drop_index(
        "ix_company_researches_project_id", table_name="company_researches"
    )
    op.drop_table("company_researches")
