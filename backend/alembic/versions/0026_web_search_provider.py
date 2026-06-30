"""contact_discoveries に web_search_provider 列を追加

Web Research で実際に使用した検索プロバイダー（brave / serpapi / tavily /
google_cse / duckduckgo）を記録し、UI に表示する。既存データには影響しない
（nullable）。

Revision ID: 0026_web_search_provider
Revises: 0025_web_research_strategy
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_web_search_provider"
down_revision: Union[str, None] = "0025_web_research_strategy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contact_discoveries",
        sa.Column("web_search_provider", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contact_discoveries", "web_search_provider")
