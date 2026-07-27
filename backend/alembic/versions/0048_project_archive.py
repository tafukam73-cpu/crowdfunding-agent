"""営業対象外（ソフトデリート）カラムを projects に追加

営業価値の低い案件を「営業対象外」として一覧・ランキング・Today Tasks・Sales
Copilot・営業対象一覧・送信後フォローから除外できるようにする。完全削除ではなく
ソフトデリート方式で、関連する調査結果や営業履歴（ContactDiscovery / SalesOutreach
/ CRM など）は一切削除しない。「除外済み案件」画面から復元できる。

追加カラム（すべて nullable＝既存行は「対象外ではない」まま保持）:
- projects.archived_at    … 営業対象外にした日時（NULL なら対象内）。is_archived は
                            archived_at IS NOT NULL で導出する。
- projects.archive_reason … 営業対象外にした理由（選択式ラベルまたは自由入力）。
                            将来の分析に使えるよう保存する。

方針：追加のみ・非破壊。

Revision ID: 0048_project_archive
Revises: 0047_contact_search_gate
Create Date: 2026-07-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0048_project_archive"
down_revision: Union[str, None] = "0047_contact_search_gate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "projects", sa.Column("archive_reason", sa.Text(), nullable=True)
    )
    op.create_index("ix_projects_archived_at", "projects", ["archived_at"])


def downgrade() -> None:
    op.drop_index("ix_projects_archived_at", table_name="projects")
    op.drop_column("projects", "archive_reason")
    op.drop_column("projects", "archived_at")
