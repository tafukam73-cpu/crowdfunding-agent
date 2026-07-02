"""sales_opportunities テーブルを追加（Contact Intelligence v5：AI営業案件管理）

Contact Intelligence の結果を営業活動に変換して管理する。連絡先探索（
contact_discoveries）1 件につき 1 営業案件（重複作成防止のため unique）。

Revision ID: 0034_sales_opps
Revises: 0033_recursive_crawl
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034_sales_opps"
down_revision: Union[str, None] = "0033_recursive_crawl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "contact_discovery_id", sa.Integer(),
            sa.ForeignKey("contact_discoveries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("company_name", sa.Text(), nullable=True),
        sa.Column("product_name", sa.Text(), nullable=True),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("sales_score", sa.Integer(), nullable=True),
        sa.Column("sales_priority", sa.String(length=10), nullable=True),
        sa.Column("recommended_channel", sa.String(length=40), nullable=True),
        sa.Column("primary_email", sa.Text(), nullable=True),
        sa.Column("contact_form_url", sa.Text(), nullable=True),
        sa.Column("primary_social_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="not_started"),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("next_action_due_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_sales_opps_contact_discovery_id", "sales_opportunities",
        ["contact_discovery_id"], unique=True,
    )
    op.create_index("ix_sales_opps_status", "sales_opportunities", ["status"])
    op.create_index("ix_sales_opps_sales_score", "sales_opportunities", ["sales_score"])
    op.create_index("ix_sales_opps_sales_priority", "sales_opportunities", ["sales_priority"])
    op.create_index(
        "ix_sales_opps_next_action_due_date", "sales_opportunities",
        ["next_action_due_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_sales_opps_next_action_due_date", table_name="sales_opportunities")
    op.drop_index("ix_sales_opps_sales_priority", table_name="sales_opportunities")
    op.drop_index("ix_sales_opps_sales_score", table_name="sales_opportunities")
    op.drop_index("ix_sales_opps_status", table_name="sales_opportunities")
    op.drop_index("ix_sales_opps_contact_discovery_id", table_name="sales_opportunities")
    op.drop_table("sales_opportunities")
