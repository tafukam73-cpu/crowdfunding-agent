"""contact_discoveries に Web Research 検索戦略デバッグ列を追加

複合検索クエリ戦略（要件）の可視化のため、生成したキーワード候補・生成クエリ全体・
検索結果のスコアリング履歴（採用/除外理由つき）を保存する。既存の web_* 列を補う
デバッグ用カラムで、既存データには影響しない（すべて nullable）。

Revision ID: 0025_web_research_strategy
Revises: 0024_contact_people
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_web_research_strategy"
down_revision: Union[str, None] = "0024_contact_people"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contact_discoveries",
        sa.Column("web_keyword_candidates", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_generated_queries", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_search_results", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contact_discoveries", "web_search_results")
    op.drop_column("contact_discoveries", "web_generated_queries")
    op.drop_column("contact_discoveries", "web_keyword_candidates")
