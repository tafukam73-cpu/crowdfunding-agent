"""Contact Intelligence ジョブを専用ワーカープロセスで実行するためのカラム追加

重い探索（full/web/document/search/ai/contact_discovery(_v2)）を API（uvicorn）
プロセス内のデーモンスレッドで動かすと、外部取得のハングや CPU スピンがイベント
ループを飢餓状態にして /health すら無応答になる。実行を独立した cfagent-ci-worker
プロセスへ分離し、PostgreSQL をジョブキューとして使う。そのためのカラムを追加する。

追加カラム（すべて nullable もしくは server_default 付き＝既存行を保持）:
- worker_id        … ジョブを claim したワーカーの識別子
- execution_token  … claim ごとに発行する一意トークン（二重実行防止・所有権確認）
- heartbeat_at     … ワーカーの生存更新時刻（stale 検出＝ワーカー死亡時の回収に使う）
- cancel_requested … 中断要求フラグ（API が true にし、ワーカーが検知して停止・kill）

方針：追加のみ・非破壊。既存 contact_intelligence_jobs 行は保持する。

Revision ID: 0046_ci_worker_columns
Revises: 0045_post_send_outreach
Create Date: 2026-07-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0046_ci_worker_columns"
down_revision: Union[str, None] = "0045_post_send_outreach"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "contact_intelligence_jobs",
        sa.Column("worker_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "contact_intelligence_jobs",
        sa.Column("execution_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "contact_intelligence_jobs",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "contact_intelligence_jobs",
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # queued ジョブの claim（status + id 順）を速くするための複合インデックス。
    op.create_index(
        "ix_ci_jobs_status_id",
        "contact_intelligence_jobs",
        ["status", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ci_jobs_status_id", table_name="contact_intelligence_jobs")
    op.drop_column("contact_intelligence_jobs", "cancel_requested")
    op.drop_column("contact_intelligence_jobs", "heartbeat_at")
    op.drop_column("contact_intelligence_jobs", "execution_token")
    op.drop_column("contact_intelligence_jobs", "worker_id")
