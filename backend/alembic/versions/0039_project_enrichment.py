"""projects に enrichment（詳細補完の根拠）JSON カラムを追加

詳細ページ（Zeczec 等）から補完した根拠情報を、再スクレイプで消えない場所に保存する。
一覧スクレイパーは ProjectCreate の項目のみ upsert するため、この列には触れない。

保存する内容（例）:
  {
    "creator_url": "https://www.zeczec.com/users/3743542",
    "brand_name": "MORESIE",
    "product_description": "...",
    "project_type": "預購式專案",
    "official_site_candidates": [{"url","confidence","source"}],
    "socials": {"instagram": "..."},
    "source_detail_url": "https://www.zeczec.com/projects/inopro",
    "enriched_at": "2026-07-10T...",
    "reasons": {"category": "...", "official_site": "..."},
    "provenance": {"maker_name": "zeczec_detail:提案人", ...}
  }

追加のみ（既存テーブル・データは変更しない）。nullable。

Revision ID: 0039_project_enrichment
Revises: 0038_contact_discovery_v2
Create Date: 2026-07-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039_project_enrichment"
down_revision: Union[str, None] = "0038_contact_discovery_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("enrichment", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "enrichment")
