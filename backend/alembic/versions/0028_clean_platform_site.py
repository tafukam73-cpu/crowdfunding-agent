"""contact_discoveries の official_site_url からクラファン/プラットフォーム URL を除去

公式サイトをクラウドファンディング/集約プラットフォーム URL（kickstarter.com/
profile/... 等）と誤判定して保存していた既存行をクリーンアップする。該当する
official_site_url を NULL に更新する（UI で「公式サイト未発見」と表示され、再探索で
実際の企業ドメインに更新される）。データの削除はしない。

Revision ID: 0028_clean_platform_site
Revises: 0027_web_debug_counts
Create Date: 2026-07-01

注: revision 識別子は alembic_version.version_num（varchar(32)）に収まるよう
    32 文字以内にする。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_clean_platform_site"
down_revision: Union[str, None] = "0027_web_debug_counts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 公式サイトに採用しないプラットフォームドメイン（contact_discovery_service と一致）。
_PLATFORM_DOMAINS = (
    "kickstarter.com", "indiegogo.com", "ulule.com", "makuake.com",
    "camp-fire.jp", "campfire.jp", "greenfunding.jp", "readyfor.jp",
    "wadiz.kr", "wadiz.co.kr", "gofundme.com", "patreon.com",
    "crowdfunder.co.uk", "fundrazr.com", "machi-ya.jp", "machiya.jp",
    "for-good.net",
)


def upgrade() -> None:
    # sa.text() の名前付きバインドパラメータ（:pat）を使う。SQLAlchemy が各 DB
    # ドライバの paramstyle（PostgreSQL/psycopg は %(name)s、SQLite は ?）へ
    # 変換するため、PostgreSQL・SQLite の両方で安全に動作する。
    # exec_driver_sql は変換しないため使わない（PostgreSQL で ":" が構文エラーになる）。
    conn = op.get_bind()
    stmt = sa.text(
        "UPDATE contact_discoveries SET official_site_url = NULL "
        "WHERE official_site_url LIKE :pat"
    )
    for d in _PLATFORM_DOMAINS:
        conn.execute(stmt, {"pat": f"%{d}%"})


def downgrade() -> None:
    # データ復元はできない（NULL 化した値は保持していない）。何もしない。
    pass
