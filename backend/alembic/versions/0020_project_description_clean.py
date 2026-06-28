"""projects に description_clean カラムを追加

スクレイパー保存時の生 HTML（figure / img / script / style / エンティティ等）を
除去した読みやすい概要を保存する。過去データは null のまま（表示側で sanitize ）。
全件再生成は `python -m scripts.rebuild_project_descriptions` で行う。

Revision ID: 0020_project_description_clean
Revises: 0019_sales_status
Create Date: 2026-06-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_project_description_clean"
down_revision: Union[str, None] = "0019_sales_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("description_clean", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "description_clean")
