"""contact_discoveries に web_search_diagnostics 列を追加

各検索クエリの診断（provider/status/reason/results/fallback/URL）を保存し、
「検索結果0件」の原因（401/403/429/キー未設定/parse error/空/例外/DDGフォール
バック）を UI とログで可視化する。nullable。

Revision ID: 0031_search_diag
Revises: 0030_search_agent
Create Date: 2026-07-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031_search_diag"
down_revision: Union[str, None] = "0030_search_agent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contact_discoveries",
        sa.Column("web_search_diagnostics", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contact_discoveries", "web_search_diagnostics")
