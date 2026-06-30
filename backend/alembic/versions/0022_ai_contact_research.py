"""contact_discoveries に AI 連絡先リサーチ列を追加

HTML 抽出で見つからない/低品質な場合に AI（Claude / モック）が推定・整理した
連絡先候補（主要メール・候補メール・問い合わせフォーム・SNS・検索クエリ・出典・
推奨チャネル・確度・メモ）を保存する。既存の自動抽出カラムは保持し、AI 結果は
ai_* に分離して保存する（自動抽出を無条件上書きしない）。

Revision ID: 0022_ai_contact_research
Revises: 0021_japan_sales_check
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_ai_contact_research"
down_revision: Union[str, None] = "0021_japan_sales_check"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contact_discoveries",
        sa.Column(
            "ai_researched",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("ai_primary_email", sa.Text(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("ai_contact_form_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("ai_instagram_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("ai_facebook_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("ai_linkedin_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("ai_candidate_emails", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("ai_search_queries", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("ai_sources", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("ai_confidence_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("ai_recommended_channel", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("ai_notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("ai_model", sa.String(length=60), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("ai_researched_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contact_discoveries", "ai_researched_at")
    op.drop_column("contact_discoveries", "ai_model")
    op.drop_column("contact_discoveries", "ai_notes")
    op.drop_column("contact_discoveries", "ai_recommended_channel")
    op.drop_column("contact_discoveries", "ai_confidence_score")
    op.drop_column("contact_discoveries", "ai_sources")
    op.drop_column("contact_discoveries", "ai_search_queries")
    op.drop_column("contact_discoveries", "ai_candidate_emails")
    op.drop_column("contact_discoveries", "ai_linkedin_url")
    op.drop_column("contact_discoveries", "ai_facebook_url")
    op.drop_column("contact_discoveries", "ai_instagram_url")
    op.drop_column("contact_discoveries", "ai_contact_form_url")
    op.drop_column("contact_discoveries", "ai_primary_email")
    op.drop_column("contact_discoveries", "ai_researched")
