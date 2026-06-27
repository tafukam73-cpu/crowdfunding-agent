"""reply_assistants テーブル作成

海外メーカーからの返信メールを AI 解析し、日本語要約・意図・返信案・Gmail 返信
下書きを支援する。

Revision ID: 0017_reply_assistant
Revises: 0016_contact_discovery
Create Date: 2026-06-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_reply_assistant"
down_revision: Union[str, None] = "0016_contact_discovery"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reply_assistants",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("maker_id", sa.Integer(), nullable=True),
        sa.Column("incoming_subject", sa.Text(), nullable=True),
        sa.Column("incoming_body", sa.Text(), nullable=False),
        sa.Column("incoming_from", sa.Text(), nullable=True),
        sa.Column("detected_language", sa.String(length=20), nullable=True),
        sa.Column("japanese_summary", sa.Text(), nullable=True),
        sa.Column("intent", sa.String(length=40), nullable=True),
        sa.Column("sentiment", sa.String(length=20), nullable=True),
        sa.Column("key_points", sa.JSON(), nullable=True),
        sa.Column("requested_actions", sa.JSON(), nullable=True),
        sa.Column("risks_or_cautions", sa.JSON(), nullable=True),
        sa.Column("recommended_next_action", sa.Text(), nullable=True),
        sa.Column("reply_tone", sa.String(length=20), nullable=True),
        sa.Column("reply_subject", sa.Text(), nullable=True),
        sa.Column("reply_body", sa.Text(), nullable=True),
        sa.Column("gmail_draft_id", sa.Text(), nullable=True),
        sa.Column("gmail_web_link", sa.Text(), nullable=True),
        sa.Column("model", sa.String(length=60), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="draft"
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_reply_assistants_project_id", "reply_assistants", ["project_id"]
    )
    op.create_index("ix_reply_assistants_maker_id", "reply_assistants", ["maker_id"])
    op.create_index("ix_reply_assistants_intent", "reply_assistants", ["intent"])
    op.create_index("ix_reply_assistants_sentiment", "reply_assistants", ["sentiment"])
    op.create_index("ix_reply_assistants_status", "reply_assistants", ["status"])


def downgrade() -> None:
    op.drop_index("ix_reply_assistants_status", table_name="reply_assistants")
    op.drop_index("ix_reply_assistants_sentiment", table_name="reply_assistants")
    op.drop_index("ix_reply_assistants_intent", table_name="reply_assistants")
    op.drop_index("ix_reply_assistants_maker_id", table_name="reply_assistants")
    op.drop_index("ix_reply_assistants_project_id", table_name="reply_assistants")
    op.drop_table("reply_assistants")
