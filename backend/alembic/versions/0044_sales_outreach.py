"""営業実行パイプライン（sales_outreach）テーブルを追加

Discovery → Promote → Contact Intelligence → 営業実行 を一本化する最終段。
案件 1 件につき 1 本の営業アウトリーチ（下書き〜成約までのライフサイクル）を管理する。

- 追加のみ（既存テーブル・既存データは変更しない・非破壊）。
- project_id は projects.id への外部キー（ondelete=CASCADE）かつ unique
  （1 案件 1 outreach。二重下書き作成を防ぐ）。
- outreach_status は NOT NULL・server_default='draft'。

Revision ID: 0044_sales_outreach
Revises: 0043_discovery_promo
Create Date: 2026-07-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044_sales_outreach"
down_revision: Union[str, None] = "0043_discovery_promo"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sales_outreach",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column(
            "outreach_status",
            sa.String(length=20),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("priority_score", sa.Integer(), nullable=True),
        sa.Column("generated_subject", sa.Text(), nullable=True),
        sa.Column("generated_body", sa.Text(), nullable=True),
        sa.Column("generated_language", sa.String(length=8), nullable=True),
        sa.Column("generated_variants", sa.JSON(), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("project_id", name="uq_sales_outreach_project_id"),
    )
    op.create_index(
        "ix_sales_outreach_project_id", "sales_outreach", ["project_id"]
    )
    op.create_index(
        "ix_sales_outreach_outreach_status", "sales_outreach", ["outreach_status"]
    )
    op.create_index(
        "ix_sales_outreach_priority_score", "sales_outreach", ["priority_score"]
    )


def downgrade() -> None:
    op.drop_index("ix_sales_outreach_priority_score", table_name="sales_outreach")
    op.drop_index("ix_sales_outreach_outreach_status", table_name="sales_outreach")
    op.drop_index("ix_sales_outreach_project_id", table_name="sales_outreach")
    op.drop_table("sales_outreach")
