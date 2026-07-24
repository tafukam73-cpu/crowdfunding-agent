"""日本クラファン適性ゲート（メール探索の事前判定）の結果カラムを追加

日本のクラウドファンディングに適さない商品へメール探索を走らせないため、判定結果を
projects にキャッシュする。スコア自体は既存の sales_assessments（makuake_fit＝日本
クラファン適性）を再利用し、ここには**判定結果だけ**を保存する（既存ランキングや
既存案件のスコアは変更しない）。

追加カラム（すべて nullable＝既存行は「未判定」のまま保持）:
- projects.eligible_for_contact_search … メール探索へ進めてよいか
- projects.contact_search_gate_reason  … 判定理由（不合格理由・合格理由）
- projects.japan_crowdfunding_score    … 判定時の日本クラファン適性スコア
- projects.gate_checked_at             … 判定日時
- contact_intelligence_jobs.gate_override_reason … 管理者が手動実行したときの理由

campaign_url（海外クラファン商品ページ URL）は **新カラムを追加しない**。既存の
projects.source_url が正規フィールドで、source_site との整合チェック（app.services.
campaign_url）を通したものを campaign_url として公開する。

方針：追加のみ・非破壊。

Revision ID: 0047_contact_search_gate
Revises: 0046_ci_worker_columns
Create Date: 2026-07-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047_contact_search_gate"
down_revision: Union[str, None] = "0046_ci_worker_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("eligible_for_contact_search", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "projects", sa.Column("contact_search_gate_reason", sa.Text(), nullable=True)
    )
    op.add_column(
        "projects", sa.Column("japan_crowdfunding_score", sa.Integer(), nullable=True)
    )
    op.add_column(
        "projects", sa.Column("gate_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_projects_eligible_for_contact_search",
        "projects",
        ["eligible_for_contact_search"],
    )
    op.add_column(
        "contact_intelligence_jobs",
        sa.Column("gate_override_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contact_intelligence_jobs", "gate_override_reason")
    op.drop_index("ix_projects_eligible_for_contact_search", table_name="projects")
    op.drop_column("projects", "gate_checked_at")
    op.drop_column("projects", "japan_crowdfunding_score")
    op.drop_column("projects", "contact_search_gate_reason")
    op.drop_column("projects", "eligible_for_contact_search")
