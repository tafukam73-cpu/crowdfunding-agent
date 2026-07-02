"""discovered_products テーブルを追加（Discovery Engine v1-1：発掘商品候補の土台）

海外クラウドファンディング等から発掘した商品候補を保存・一覧取得する土台。
source_url をユニークキーとして重複登録を防ぐ。AI スコアリングは将来実装のため
スコア系カラムは nullable。

Revision ID: 0035_discovered_products
Revises: 0034_sales_opps
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035_discovered_products"
down_revision: Union[str, None] = "0034_sales_opps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "discovered_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_platform", sa.String(length=30), nullable=False,
                  server_default="other"),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("project_title", sa.Text(), nullable=True),
        sa.Column("creator_name", sa.String(length=255), nullable=True),
        sa.Column("product_name", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("country", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="unknown"),
        sa.Column("funding_amount", sa.Numeric(16, 2), nullable=True),
        sa.Column("funding_goal", sa.Numeric(16, 2), nullable=True),
        sa.Column("backers_count", sa.BigInteger(), nullable=True),
        sa.Column("launch_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("official_website_url", sa.Text(), nullable=True),
        sa.Column("japan_fit_score", sa.Integer(), nullable=True),
        sa.Column("crowdfunding_fit_score", sa.Integer(), nullable=True),
        sa.Column("novelty_score", sa.Integer(), nullable=True),
        sa.Column("logistics_score", sa.Integer(), nullable=True),
        sa.Column("regulatory_risk_score", sa.Integer(), nullable=True),
        sa.Column("competition_risk_score", sa.Integer(), nullable=True),
        sa.Column("japan_entry_risk_score", sa.Integer(), nullable=True),
        sa.Column("overall_discovery_score", sa.Integer(), nullable=True),
        sa.Column("discovery_reasoning", sa.Text(), nullable=True),
        sa.Column("recommended_next_action", sa.Text(), nullable=True),
        sa.Column("contact_discovery_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_discovered_products_source_url", "discovered_products",
        ["source_url"], unique=True,
    )
    op.create_index(
        "ix_discovered_products_source_platform", "discovered_products",
        ["source_platform"],
    )
    op.create_index(
        "ix_discovered_products_status", "discovered_products", ["status"],
    )
    op.create_index(
        "ix_discovered_products_category", "discovered_products", ["category"],
    )
    op.create_index(
        "ix_discovered_products_overall_discovery_score", "discovered_products",
        ["overall_discovery_score"],
    )
    op.create_index(
        "ix_discovered_products_contact_discovery_id", "discovered_products",
        ["contact_discovery_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_discovered_products_contact_discovery_id",
                  table_name="discovered_products")
    op.drop_index("ix_discovered_products_overall_discovery_score",
                  table_name="discovered_products")
    op.drop_index("ix_discovered_products_category",
                  table_name="discovered_products")
    op.drop_index("ix_discovered_products_status",
                  table_name="discovered_products")
    op.drop_index("ix_discovered_products_source_platform",
                  table_name="discovered_products")
    op.drop_index("ix_discovered_products_source_url",
                  table_name="discovered_products")
    op.drop_table("discovered_products")
