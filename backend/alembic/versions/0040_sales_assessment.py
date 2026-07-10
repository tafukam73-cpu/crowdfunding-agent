"""sales_assessments テーブルを追加（Sales Copilot v2：営業適性スコア）

営業対象案件（projects）に対する日本市場適性 / 独占販売可能性 / Makuake 適性の
3 スコアと総合優先度を保存する。追加のみ（既存テーブル・データは変更しない）。

Revision ID: 0040_sales_assessment
Revises: 0039_project_enrichment
Create Date: 2026-07-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040_sales_assessment"
down_revision: Union[str, None] = "0039_project_enrichment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_assessments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("japan_market_fit_score", sa.Integer(), nullable=True),
        sa.Column("exclusivity_score", sa.Integer(), nullable=True),
        sa.Column("makuake_fit_score", sa.Integer(), nullable=True),
        sa.Column("overall_priority_score", sa.Integer(), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("engine", sa.String(length=60), nullable=False),
        sa.Column("ai_adjusted", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sales_assessments_project_id", "sales_assessments",
                    ["project_id"])
    op.create_index("ix_sales_assessments_overall_priority_score",
                    "sales_assessments", ["overall_priority_score"])
    op.create_index("ix_sales_assessments_japan_market_fit_score",
                    "sales_assessments", ["japan_market_fit_score"])
    op.create_index("ix_sales_assessments_exclusivity_score",
                    "sales_assessments", ["exclusivity_score"])
    op.create_index("ix_sales_assessments_makuake_fit_score",
                    "sales_assessments", ["makuake_fit_score"])


def downgrade() -> None:
    for idx in (
        "ix_sales_assessments_makuake_fit_score",
        "ix_sales_assessments_exclusivity_score",
        "ix_sales_assessments_japan_market_fit_score",
        "ix_sales_assessments_overall_priority_score",
        "ix_sales_assessments_project_id",
    ):
        op.drop_index(idx, table_name="sales_assessments")
    op.drop_table("sales_assessments")
