"""送信後営業ワークフロー用カラムを sales_outreach に追加

Discovery → Promote → Contact Intelligence → 営業実行（生成）の次段として、
「送信済みとして記録 → フォローアップ → 返信登録」までの送信後ワークフローを扱う。

追加カラム（すべて nullable、または server_default 付き）:
- recipient_email / sent_subject / sent_body_snapshot / sent_language
  … 「送信済みとして記録」時の宛先・件名・本文・言語のスナップショット
- followup_due_at / followup_count / last_followup_at
  … フォローアップの期日・回数・直近実施時刻
- reply_intent / reply_summary / reply_confidence / last_reply_at
  … 手動登録した返信の解析結果
- user_edited / edited_at
  … ユーザーが下書きを編集したか（AI 再生成の上書き防止）

方針:
- 追加のみ（既存テーブル・既存 sales_outreach 行は保持・非破壊）。
- 実際のメール送信はしない（送信済み記録はユーザー操作のみ）。

Revision ID: 0045_post_send_outreach
Revises: 0044_sales_outreach
Create Date: 2026-07-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0045_post_send_outreach"
down_revision: Union[str, None] = "0044_sales_outreach"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 送信時スナップショット
    op.add_column(
        "sales_outreach",
        sa.Column("recipient_email", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "sales_outreach", sa.Column("sent_subject", sa.Text(), nullable=True)
    )
    op.add_column(
        "sales_outreach", sa.Column("sent_body_snapshot", sa.Text(), nullable=True)
    )
    op.add_column(
        "sales_outreach",
        sa.Column("sent_language", sa.String(length=8), nullable=True),
    )

    # フォローアップ
    op.add_column(
        "sales_outreach",
        sa.Column("followup_due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sales_outreach",
        sa.Column(
            "followup_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "sales_outreach",
        sa.Column("last_followup_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 返信の手動登録
    op.add_column(
        "sales_outreach",
        sa.Column("reply_intent", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "sales_outreach", sa.Column("reply_summary", sa.Text(), nullable=True)
    )
    op.add_column(
        "sales_outreach",
        sa.Column("reply_confidence", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "sales_outreach",
        sa.Column("last_reply_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ユーザー編集の保護
    op.add_column(
        "sales_outreach",
        sa.Column(
            "user_edited",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "sales_outreach",
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_sales_outreach_followup_due_at", "sales_outreach", ["followup_due_at"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sales_outreach_followup_due_at", table_name="sales_outreach"
    )
    for col in (
        "edited_at",
        "user_edited",
        "last_reply_at",
        "reply_confidence",
        "reply_summary",
        "reply_intent",
        "last_followup_at",
        "followup_count",
        "followup_due_at",
        "sent_language",
        "sent_body_snapshot",
        "sent_subject",
        "recipient_email",
    ):
        op.drop_column("sales_outreach", col)
