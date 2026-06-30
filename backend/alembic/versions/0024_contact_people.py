"""contact_people テーブル作成 + contacts に department / linkedin_url 追加

Contact Hunter AI が発見した営業担当者候補（氏名・役職・部署・LinkedIn・メール・
出典・信頼度・営業優先度）を保存する。あわせて CRM の担当者（contacts）にも部署と
LinkedIn を保存できるようにする。

Revision ID: 0024_contact_people
Revises: 0023_web_research
Create Date: 2026-06-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_contact_people"
down_revision: Union[str, None] = "0023_web_research"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contact_people",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("department", sa.String(length=80), nullable=True),
        sa.Column("linkedin_url", sa.Text(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("email_source", sa.String(length=40), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        "ix_contact_people_project_id", "contact_people", ["project_id"]
    )

    # CRM の担当者に部署 / LinkedIn を追加
    op.add_column("contacts", sa.Column("department", sa.String(length=80), nullable=True))
    op.add_column("contacts", sa.Column("linkedin_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("contacts", "linkedin_url")
    op.drop_column("contacts", "department")
    op.drop_index("ix_contact_people_project_id", table_name="contact_people")
    op.drop_table("contact_people")
