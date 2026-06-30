"""contact_discoveries に AI Web Research Mode 列を追加

検索エンジン（DuckDuckGo HTML）＋公式サイト横断クロールで実際に取得した連絡先を
保存する。AI Contact Research（ai_*）/ 自動抽出とは別レイヤーとして web_* に分離
保存し、既存データを無条件上書きしない。発見メールは既存の除外フィルタを通過した
ものだけを保存する（出典付き）。

Revision ID: 0023_web_research
Revises: 0022_ai_contact_research
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_web_research"
down_revision: Union[str, None] = "0022_ai_contact_research"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contact_discoveries",
        sa.Column(
            "web_researched",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_searched_queries", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_searched_urls", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_candidate_pages", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_discovered_emails", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_discovered_forms", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_discovered_socials", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_discovered_pdfs", sa.JSON(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_primary_email", sa.Text(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_primary_contact_form_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_recommended_channel", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_confidence_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_evidence_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_research_error", sa.Text(), nullable=True),
    )
    op.add_column(
        "contact_discoveries",
        sa.Column("web_researched_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contact_discoveries", "web_researched_at")
    op.drop_column("contact_discoveries", "web_research_error")
    op.drop_column("contact_discoveries", "web_notes")
    op.drop_column("contact_discoveries", "web_evidence_summary")
    op.drop_column("contact_discoveries", "web_confidence_score")
    op.drop_column("contact_discoveries", "web_recommended_channel")
    op.drop_column("contact_discoveries", "web_primary_contact_form_url")
    op.drop_column("contact_discoveries", "web_primary_email")
    op.drop_column("contact_discoveries", "web_discovered_pdfs")
    op.drop_column("contact_discoveries", "web_discovered_socials")
    op.drop_column("contact_discoveries", "web_discovered_forms")
    op.drop_column("contact_discoveries", "web_discovered_emails")
    op.drop_column("contact_discoveries", "web_candidate_pages")
    op.drop_column("contact_discoveries", "web_searched_urls")
    op.drop_column("contact_discoveries", "web_searched_queries")
    op.drop_column("contact_discoveries", "web_researched")
