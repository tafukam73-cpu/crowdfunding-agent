"""営業対象除外判定：判定履歴テーブルと一覧用キャッシュ列を追加

Lead Qualification Engine（LQE）の判定結果を追記専用で保存する
lead_qualifications テーブルを追加し、一覧のフィルタ/ソート用に
projects へ nullable 列を 2 つ追加する。

方針：追加のみ・非破壊。既存カラム・既存データには一切変更を加えない。
- カラム型変更なし / NOT NULL 追加なし / 既存データ更新なし / テーブル削除なし
- lead_qualifications は append-only（アプリ側で UPDATE / DELETE しない）
- projects の既存列（status / sales_status / archived_at / archive_reason など）は
  一切触らない

Revision ID: 0050_lead_qualification
Revises: 0049_project_status_events
Create Date: 2026-08-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0050_lead_qualification"
down_revision: Union[str, None] = "0049_project_status_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lead_qualifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("decision", sa.String(length=12), nullable=False),
        sa.Column("blocker_codes", sa.JSON(), nullable=True),
        sa.Column("review_codes", sa.JSON(), nullable=True),
        sa.Column("findings_json", sa.JSON(), nullable=True),
        sa.Column("positive_facts_json", sa.JSON(), nullable=True),
        sa.Column("evidence_count", sa.Integer(), nullable=True),
        sa.Column("engine", sa.String(length=60), nullable=False),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("override_evidence_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_lead_qualifications_project_id", "lead_qualifications", ["project_id"]
    )
    op.create_index("ix_lead_qualifications_stage", "lead_qualifications", ["stage"])
    op.create_index(
        "ix_lead_qualifications_decision", "lead_qualifications", ["decision"]
    )
    op.create_index(
        "ix_lead_qualifications_created_at", "lead_qualifications", ["created_at"]
    )

    # 一覧のフィルタ/ソート用キャッシュ（判定の正本は lead_qualifications）。
    op.add_column(
        "projects",
        sa.Column("lead_qualification_decision", sa.String(length=12), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("lead_qualification_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_projects_lead_qualification_decision",
        "projects",
        ["lead_qualification_decision"],
    )


def downgrade() -> None:
    op.drop_index("ix_projects_lead_qualification_decision", table_name="projects")
    op.drop_column("projects", "lead_qualification_at")
    op.drop_column("projects", "lead_qualification_decision")
    op.drop_index("ix_lead_qualifications_created_at", table_name="lead_qualifications")
    op.drop_index("ix_lead_qualifications_decision", table_name="lead_qualifications")
    op.drop_index("ix_lead_qualifications_stage", table_name="lead_qualifications")
    op.drop_index("ix_lead_qualifications_project_id", table_name="lead_qualifications")
    op.drop_table("lead_qualifications")
