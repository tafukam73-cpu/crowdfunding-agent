"""contact_discoveries に AI Document Reader 列を追加

ページ全体を読解する追加レイヤー（AI Document Reader）の結果を doc_reader_* に
分離保存する。自動抽出 / AI 調査 / Web 調査を無条件上書きしない。すべて nullable。

Revision ID: 0029_doc_reader
Revises: 0028_clean_platform_site
Create Date: 2026-07-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029_doc_reader"
down_revision: Union[str, None] = "0028_clean_platform_site"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("contact_discoveries", sa.Column("doc_reader_researched", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("contact_discoveries", sa.Column("doc_reader_model", sa.String(length=60), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_official_company_name", sa.Text(), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_brand_names", sa.JSON(), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_official_site_url", sa.Text(), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_emails", sa.JSON(), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_contact_forms", sa.JSON(), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_socials", sa.JSON(), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_people", sa.JSON(), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_recommended_channel", sa.String(length=40), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_recommended_contact", sa.Text(), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_confidence_score", sa.Integer(), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_evidence_summary", sa.Text(), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_missing_info", sa.JSON(), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_sources", sa.JSON(), nullable=True))
    op.add_column("contact_discoveries", sa.Column("doc_reader_researched_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for col in (
        "doc_reader_researched_at", "doc_reader_sources", "doc_reader_missing_info",
        "doc_reader_evidence_summary", "doc_reader_confidence_score",
        "doc_reader_recommended_contact", "doc_reader_recommended_channel",
        "doc_reader_people", "doc_reader_socials", "doc_reader_contact_forms",
        "doc_reader_emails", "doc_reader_official_site_url", "doc_reader_brand_names",
        "doc_reader_official_company_name", "doc_reader_model", "doc_reader_researched",
    ):
        op.drop_column("contact_discoveries", col)
