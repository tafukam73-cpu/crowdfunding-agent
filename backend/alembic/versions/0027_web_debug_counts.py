"""contact_discoveries に Web Research のデバッグ集計列を追加

探索処理がどこまで進んだかを可視化するため、集計（クエリ数・検索結果件数・巡回URL
数・成功/失敗/除外/メール抽出対象ページ数）と探索フローの要約を保存する。既存
データには影響しない（nullable）。

Revision ID: 0027_web_debug_counts
Revises: 0026_web_search_provider
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_web_debug_counts"
down_revision: Union[str, None] = "0026_web_search_provider"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contact_discoveries",
        sa.Column("web_debug_counts", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_research_flow", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contact_discoveries", "web_research_flow")
    op.drop_column("contact_discoveries", "web_debug_counts")
