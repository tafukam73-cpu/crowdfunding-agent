"""Discovery → Projects 昇格ワークフローの状態列を追加

discovered_products に昇格状態（promotion_status）と昇格先 project への参照
（promoted_project_id / promoted_at / promotion_error）を追加する。

- 追加のみ（既存列・既存データは変更しない）。
- promotion_status は NOT NULL・server_default='not_promoted' のため、既存行は
  自動的に not_promoted 扱いになる（非破壊）。
- promoted_project_id / promoted_at / promotion_error は nullable。
- promoted_project_id は projects.id への外部キー（ondelete=SET NULL）。project が
  万一削除されても discovered_products を壊さず NULL に落とす（非破壊・安全側）。

Revision ID: 0043_discovery_promo
Revises: 0042_discovery_wz
Create Date: 2026-07-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043_discovery_promo"
down_revision: Union[str, None] = "0042_discovery_wz"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "discovered_products",
        sa.Column(
            "promotion_status",
            sa.String(length=20),
            nullable=False,
            server_default="not_promoted",
        ),
    )
    op.add_column(
        "discovered_products",
        sa.Column("promoted_project_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "discovered_products",
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "discovered_products",
        sa.Column("promotion_error", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_discovered_products_promotion_status",
        "discovered_products",
        ["promotion_status"],
    )
    op.create_index(
        "ix_discovered_products_promoted_project_id",
        "discovered_products",
        ["promoted_project_id"],
    )
    # projects.id への外部キー（project 削除時は SET NULL で非破壊に外す）。
    op.create_foreign_key(
        "fk_discovered_products_promoted_project_id",
        "discovered_products",
        "projects",
        ["promoted_project_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_discovered_products_promoted_project_id",
        "discovered_products",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_discovered_products_promoted_project_id",
        table_name="discovered_products",
    )
    op.drop_index(
        "ix_discovered_products_promotion_status",
        table_name="discovered_products",
    )
    op.drop_column("discovered_products", "promotion_error")
    op.drop_column("discovered_products", "promoted_at")
    op.drop_column("discovered_products", "promoted_project_id")
    op.drop_column("discovered_products", "promotion_status")
