"""contact_discoveries に AI Search Agent 列を追加

次に見るページを判断しながら反復探索するレイヤー（AI Search Agent）の結果を
search_agent_* に分離保存する。既存レイヤーを無条件上書きしない。すべて nullable。

Revision ID: 0030_search_agent
Revises: 0029_doc_reader
Create Date: 2026-07-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_search_agent"
down_revision: Union[str, None] = "0029_doc_reader"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = [
    ("search_agent_researched", sa.Boolean(), dict(nullable=False, server_default=sa.false())),
    ("search_agent_model", sa.String(length=60), dict(nullable=True)),
    ("search_agent_status", sa.String(length=20), dict(nullable=True)),
    ("search_agent_steps", sa.JSON(), dict(nullable=True)),
    ("search_agent_searched_queries", sa.JSON(), dict(nullable=True)),
    ("search_agent_searched_urls", sa.JSON(), dict(nullable=True)),
    ("search_agent_official_site_url", sa.Text(), dict(nullable=True)),
    ("search_agent_emails", sa.JSON(), dict(nullable=True)),
    ("search_agent_contact_forms", sa.JSON(), dict(nullable=True)),
    ("search_agent_socials", sa.JSON(), dict(nullable=True)),
    ("search_agent_people", sa.JSON(), dict(nullable=True)),
    ("search_agent_recommended_channel", sa.String(length=40), dict(nullable=True)),
    ("search_agent_recommended_contact", sa.Text(), dict(nullable=True)),
    ("search_agent_confidence_score", sa.Integer(), dict(nullable=True)),
    ("search_agent_evidence_summary", sa.Text(), dict(nullable=True)),
    ("search_agent_stop_reason", sa.Text(), dict(nullable=True)),
    ("search_agent_error", sa.Text(), dict(nullable=True)),
    ("search_agent_researched_at", sa.DateTime(timezone=True), dict(nullable=True)),
]


def upgrade() -> None:
    for name, type_, kw in _COLS:
        op.add_column("contact_discoveries", sa.Column(name, type_, **kw))


def downgrade() -> None:
    for name, _type, _kw in reversed(_COLS):
        op.drop_column("contact_discoveries", name)
