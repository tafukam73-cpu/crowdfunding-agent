"""Contact Intelligence v3：公式サイト再帰クロールの結果カラムを追加

公式サイトが見つかった場合に、Contact/About だけでなくサイト全体を安全に
再帰クロール（sitemap/robots/PDF/DNS 込み）した結果を contact_discoveries に
分離保存する。

Revision ID: 0033_recursive_crawl
Revises: 0032_ci_jobs
Create Date: 2026-07-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033_recursive_crawl"
down_revision: Union[str, None] = "0032_ci_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = [
    ("recursive_crawl_enabled", sa.Boolean(), {"nullable": False, "server_default": "false"}),
    ("recursive_crawled_urls", sa.JSON(), {"nullable": True}),
    ("recursive_skipped_urls", sa.JSON(), {"nullable": True}),
    ("recursive_emails", sa.JSON(), {"nullable": True}),
    ("recursive_forms", sa.JSON(), {"nullable": True}),
    ("recursive_socials", sa.JSON(), {"nullable": True}),
    ("recursive_pdfs", sa.JSON(), {"nullable": True}),
    ("recursive_sitemap_urls", sa.JSON(), {"nullable": True}),
    ("recursive_robots_sitemaps", sa.JSON(), {"nullable": True}),
    ("recursive_has_mx", sa.Boolean(), {"nullable": True}),
    ("recursive_mx_provider", sa.String(length=40), {"nullable": True}),
    ("recursive_spf_record", sa.Text(), {"nullable": True}),
    ("recursive_dmarc_record", sa.Text(), {"nullable": True}),
    ("recursive_failure_reasons", sa.JSON(), {"nullable": True}),
    ("recursive_summary", sa.Text(), {"nullable": True}),
    ("recursive_crawled_at", sa.DateTime(timezone=True), {"nullable": True}),
]


def upgrade() -> None:
    for name, type_, kwargs in _COLUMNS:
        op.add_column("contact_discoveries", sa.Column(name, type_, **kwargs))


def downgrade() -> None:
    for name, _type, _kwargs in reversed(_COLUMNS):
        op.drop_column("contact_discoveries", name)
