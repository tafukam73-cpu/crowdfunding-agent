"""contact_discoveries の official_site_url からクラファン/プラットフォーム URL を除去

公式サイトをクラウドファンディング/集約プラットフォーム URL（kickstarter.com/
profile/... 等）と誤判定して保存していた既存行をクリーンアップする。該当する
official_site_url を NULL に更新する（UI で「公式サイト未発見」と表示され、再探索で
実際の企業ドメインに更新される）。データの削除はしない。

Revision ID: 0028_clean_platform_official_site
Revises: 0027_web_debug_counts
Create Date: 2026-07-01
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0028_clean_platform_official_site"
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
    conn = op.get_bind()
    for d in _PLATFORM_DOMAINS:
        conn.exec_driver_sql(
            "UPDATE contact_discoveries SET official_site_url = NULL "
            "WHERE official_site_url LIKE :pat",
            {"pat": f"%{d}%"},
        )


def downgrade() -> None:
    # データ復元はできない（NULL 化した値は保持していない）。何もしない。
    pass
