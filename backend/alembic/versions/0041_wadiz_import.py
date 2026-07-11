"""wadiz_imports テーブルを追加（Wadiz 手動取り込みワークフロー）

ユーザーが通常ブラウザで閲覧した Wadiz 公開ページ情報の取り込み履歴を保存する。
追加のみ（既存テーブル・データは変更しない）。

Revision ID: 0041_wadiz_import
Revises: 0040_sales_assessment
Create Date: 2026-07-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041_wadiz_import"
down_revision: Union[str, None] = "0040_sales_assessment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wadiz_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(length=20), nullable=False,
                  server_default="text"),
        sa.Column("raw_content_hash", sa.String(length=64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_by", sa.String(length=120), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("extracted_json", sa.JSON(), nullable=True),
        sa.Column("email_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_wadiz_imports_project_id", "wadiz_imports", ["project_id"])
    op.create_index("ix_wadiz_imports_raw_content_hash", "wadiz_imports",
                    ["raw_content_hash"])


def downgrade() -> None:
    op.drop_index("ix_wadiz_imports_raw_content_hash", table_name="wadiz_imports")
    op.drop_index("ix_wadiz_imports_project_id", table_name="wadiz_imports")
    op.drop_table("wadiz_imports")
