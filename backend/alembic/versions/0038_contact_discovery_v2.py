"""Contact Discovery v2：人間の検索手順に近い探索結果カラムを追加

公式サイト候補探索 → 優先クロール(Contact/About/...) → LinkedIn → メール抽出 →
検証 → 取得元による信頼度(★1〜5) の結果を contact_discoveries に分離保存する。

Revision ID: 0038_contact_discovery_v2
Revises: 0037_japan_opportunity
Create Date: 2026-07-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038_contact_discovery_v2"
down_revision: Union[str, None] = "0037_japan_opportunity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = [
    ("v2_researched", sa.Boolean(), {"nullable": False, "server_default": "false"}),
    ("v2_status", sa.String(length=20), {"nullable": True}),
    ("v2_steps", sa.JSON(), {"nullable": True}),
    ("v2_company_name", sa.Text(), {"nullable": True}),
    ("v2_product_name", sa.Text(), {"nullable": True}),
    ("v2_campaign_url", sa.Text(), {"nullable": True}),
    ("v2_official_site_url", sa.Text(), {"nullable": True}),
    ("v2_official_site_source", sa.String(length=30), {"nullable": True}),
    ("v2_official_site_candidates", sa.JSON(), {"nullable": True}),
    ("v2_crawled_pages", sa.JSON(), {"nullable": True}),
    ("v2_emails", sa.JSON(), {"nullable": True}),
    ("v2_socials", sa.JSON(), {"nullable": True}),
    ("v2_forms", sa.JSON(), {"nullable": True}),
    ("v2_linkedin_company_url", sa.Text(), {"nullable": True}),
    ("v2_linkedin_person_url", sa.Text(), {"nullable": True}),
    ("v2_linkedin_candidates", sa.JSON(), {"nullable": True}),
    ("v2_searched_queries", sa.JSON(), {"nullable": True}),
    ("v2_search_provider", sa.String(length=20), {"nullable": True}),
    ("v2_primary_email", sa.Text(), {"nullable": True}),
    ("v2_primary_source_url", sa.Text(), {"nullable": True}),
    ("v2_primary_stars", sa.Integer(), {"nullable": True}),
    ("v2_confidence_score", sa.Integer(), {"nullable": True}),
    ("v2_recommended_channel", sa.String(length=40), {"nullable": True}),
    ("v2_summary", sa.Text(), {"nullable": True}),
    ("v2_error", sa.Text(), {"nullable": True}),
    ("v2_researched_at", sa.DateTime(timezone=True), {"nullable": True}),
]


def upgrade() -> None:
    for name, type_, kwargs in _COLUMNS:
        op.add_column("contact_discoveries", sa.Column(name, type_, **kwargs))


def downgrade() -> None:
    for name, _type, _kwargs in reversed(_COLUMNS):
        op.drop_column("contact_discoveries", name)
