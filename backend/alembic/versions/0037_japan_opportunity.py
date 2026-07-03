"""japan_opportunity_analyses テーブルを追加（Japan Opportunity Engine v1-2）

Discovery Engine で発掘した商品を日本市場向けに評価した分析結果を保存する土台。
discovered_products を参照する（既存テーブルは変更しない）。スコア系は将来の評価で
埋める前提で nullable。

Revision ID: 0037_japan_opportunity
Revises: 0036_discovery_runs
Create Date: 2026-07-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037_japan_opportunity"
down_revision: Union[str, None] = "0036_discovery_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "japan_opportunity_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "discovered_product_id",
            sa.Integer(),
            sa.ForeignKey("discovered_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("japan_market_fit_score", sa.Integer(), nullable=True),
        sa.Column("japan_entry_gap_score", sa.Integer(), nullable=True),
        sa.Column("crowdfunding_fit_score", sa.Integer(), nullable=True),
        sa.Column("retail_fit_score", sa.Integer(), nullable=True),
        sa.Column("regulatory_safety_score", sa.Integer(), nullable=True),
        sa.Column("logistics_score", sa.Integer(), nullable=True),
        sa.Column("margin_potential_score", sa.Integer(), nullable=True),
        sa.Column("competition_gap_score", sa.Integer(), nullable=True),
        sa.Column("sales_success_score", sa.Integer(), nullable=True),
        sa.Column("overall_opportunity_score", sa.Integer(), nullable=True),
        sa.Column("confidence_score", sa.Integer(), nullable=True),
        sa.Column("japan_presence_summary", sa.Text(), nullable=True),
        sa.Column("competition_summary", sa.Text(), nullable=True),
        sa.Column("regulatory_summary", sa.Text(), nullable=True),
        sa.Column("logistics_summary", sa.Text(), nullable=True),
        sa.Column("pricing_summary", sa.Text(), nullable=True),
        sa.Column("opportunity_reasoning", sa.Text(), nullable=True),
        sa.Column("recommended_strategy", sa.Text(), nullable=True),
        sa.Column("recommended_next_action", sa.Text(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_japan_opportunity_analyses_discovered_product_id",
        "japan_opportunity_analyses", ["discovered_product_id"],
    )
    op.create_index(
        "ix_japan_opportunity_analyses_overall_opportunity_score",
        "japan_opportunity_analyses", ["overall_opportunity_score"],
    )
    op.create_index(
        "ix_japan_opportunity_analyses_created_at",
        "japan_opportunity_analyses", ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_japan_opportunity_analyses_created_at",
        table_name="japan_opportunity_analyses",
    )
    op.drop_index(
        "ix_japan_opportunity_analyses_overall_opportunity_score",
        table_name="japan_opportunity_analyses",
    )
    op.drop_index(
        "ix_japan_opportunity_analyses_discovered_product_id",
        table_name="japan_opportunity_analyses",
    )
    op.drop_table("japan_opportunity_analyses")
