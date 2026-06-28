"""projects に営業ワークフロー用の sales_status を追加

営業ワークフローカードが案内する営業状況（未営業→営業準備完了→営業済み→
返信待ち→返信あり→商談中→契約／見送り）。既存の status とは別軸。

Revision ID: 0019_sales_status
Revises: 0018_contact_intelligence
Create Date: 2026-06-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_sales_status"
down_revision: Union[str, None] = "0018_contact_intelligence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "sales_status",
            sa.String(length=20),
            nullable=False,
            server_default="not_started",
        ),
    )
    op.create_index(
        "ix_projects_sales_status", "projects", ["sales_status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_projects_sales_status", table_name="projects")
    op.drop_column("projects", "sales_status")
