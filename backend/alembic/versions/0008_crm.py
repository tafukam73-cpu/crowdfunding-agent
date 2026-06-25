"""CRM: makers / contacts / sales_activities 作成、projects に maker_id 追加

Revision ID: 0008_crm
Revises: 0007_job_runs
Create Date: 2026-06-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_crm"
down_revision: Union[str, None] = "0007_job_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "makers",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("website_url", sa.Text(), nullable=True),
        sa.Column("country", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="lead"),
        sa.Column("next_action", sa.Text(), nullable=True),
        sa.Column("next_action_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_makers_name", "makers", ["name"])
    op.create_index("ix_makers_status", "makers", ["status"])
    op.create_index("ix_makers_next_action_date", "makers", ["next_action_date"])

    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("maker_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=120), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=60), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["maker_id"], ["makers.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_contacts_maker_id", "contacts", ["maker_id"])

    op.create_table(
        "sales_activities",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("maker_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["maker_id"], ["makers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_sales_activities_maker_id", "sales_activities", ["maker_id"])

    op.add_column("projects", sa.Column("maker_id", sa.Integer(), nullable=True))
    op.create_index("ix_projects_maker_id", "projects", ["maker_id"])


def downgrade() -> None:
    op.drop_index("ix_projects_maker_id", table_name="projects")
    op.drop_column("projects", "maker_id")
    op.drop_index("ix_sales_activities_maker_id", table_name="sales_activities")
    op.drop_table("sales_activities")
    op.drop_index("ix_contacts_maker_id", table_name="contacts")
    op.drop_table("contacts")
    op.drop_index("ix_makers_next_action_date", table_name="makers")
    op.drop_index("ix_makers_status", table_name="makers")
    op.drop_index("ix_makers_name", table_name="makers")
    op.drop_table("makers")
