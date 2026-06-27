"""contact_discoveries に Contact Intelligence カラムを追加

メールが無くても営業可能性を総合評価する（スコア・推奨チャネル・アクション・
チェックリスト・アプローチ候補・検索クエリ・根拠サマリ）。既存カラムは保持。

Revision ID: 0018_contact_intelligence
Revises: 0017_reply_assistant
Create Date: 2026-06-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_contact_intelligence"
down_revision: Union[str, None] = "0017_reply_assistant"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contact_discoveries",
        sa.Column("contactability_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("recommended_channel", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("recommended_action", sa.Text(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("discovery_checklist", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("approach_options", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("search_queries", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("evidence_summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contact_discoveries", "evidence_summary")
    op.drop_column("contact_discoveries", "search_queries")
    op.drop_column("contact_discoveries", "approach_options")
    op.drop_column("contact_discoveries", "discovery_checklist")
    op.drop_column("contact_discoveries", "recommended_action")
    op.drop_column("contact_discoveries", "recommended_channel")
    op.drop_column("contact_discoveries", "contactability_score")
