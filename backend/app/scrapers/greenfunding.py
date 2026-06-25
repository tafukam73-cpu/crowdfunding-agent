"""GreenFunding 成功案件スクレイパー。

日本クラファン（GreenFunding）の応援購入成功案件を収集し、JapaneseSuccessCreate
のリストを返す。Makuake スクレイパーと同じインターフェース（`scrape()`）で、
比較用の日本成功事例データを蓄える。

現状はモック実装（実サイトへはアクセスしない）。後から Playwright 等による実
スクレイパーへ差し替えやすい構造にしている（差し替えフローは makuake.py 参照）。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.japanese_success import MIN_SUCCESS_JPY
from app.schemas.japanese_success import JapaneseSuccessCreate

PLATFORM = "greenfunding"

# GreenFunding の成功案件モックデータ。
# カテゴリは海外案件側（projects）と揃え、類似事例マッチングが効くようにしている。
_MOCK: list[dict] = [
    dict(
        title="耳をふさがない骨伝導オープンイヤホン「OPEN-BONE」",
        category="オーディオ",
        description="運転・ランニング中も周囲の音を聞きながら使える骨伝導イヤホン。",
        image_url="https://picsum.photos/seed/gf-bone/640/360",
        video_url="https://greenfunding.jp/project/open-bone/",
        goal_amount=Decimal("1000000.00"),
        raised_amount=Decimal("42300000.00"),
        backers_count=5210,
        start_date=date(2025, 6, 1),
        end_date=date(2025, 7, 31),
        maker_name="オープンボーン株式会社",
        maker_url="https://example.co.jp/openbone",
        slug="open-bone",
    ),
    dict(
        title="充電不要・ソーラー駆動の屋外センサーライト「SUN-LIGHT」",
        category="ガジェット",
        description="日中の太陽光で充電し夜間自動点灯。配線不要の防水センサーライト。",
        image_url="https://picsum.photos/seed/gf-light/640/360",
        video_url=None,
        goal_amount=Decimal("500000.00"),
        raised_amount=Decimal("9800000.00"),
        backers_count=2360,
        start_date=date(2025, 9, 10),
        end_date=date(2025, 10, 20),
        maker_name="サンライト合同会社",
        maker_url="https://example.co.jp/sunlight",
        slug="sun-light",
    ),
    dict(
        title="一台でキャンプ調理が完結する折りたたみ焚き火台「FIRE-FOLD」",
        category="アウトドア",
        description="薄さ3cmに折りたためる、調理対応の高耐久ステンレス焚き火台。",
        image_url="https://picsum.photos/seed/gf-fire/640/360",
        video_url="https://greenfunding.jp/project/fire-fold/",
        goal_amount=Decimal("800000.00"),
        raised_amount=Decimal("31500000.00"),
        backers_count=7180,
        start_date=date(2025, 4, 1),
        end_date=date(2025, 5, 15),
        maker_name="ファイアフォールド株式会社",
        maker_url="https://example.co.jp/firefold",
        slug="fire-fold",
    ),
    dict(
        title="空気を読んで自動調光する間接照明「MOOD-LAMP」",
        category="インテリア",
        description="室内の明るさと時間帯に応じて自動で色温度を調整するスマート照明。",
        image_url="https://picsum.photos/seed/gf-lamp/640/360",
        video_url=None,
        goal_amount=Decimal("600000.00"),
        raised_amount=Decimal("8700000.00"),
        backers_count=1640,
        start_date=date(2025, 10, 5),
        end_date=date(2025, 11, 18),
        maker_name="ムードラボ株式会社",
        maker_url="https://example.co.jp/moodlab",
        slug="mood-lamp",
    ),
    dict(
        title="洗えてたためる超軽量シリコン保存容器「FLEX-BOX」",
        category="ホーム",
        description="使わない時はぺたんこに。電子レンジ・食洗機対応の密閉シリコン容器。",
        image_url="https://picsum.photos/seed/gf-box/640/360",
        video_url="https://greenfunding.jp/project/flex-box/",
        goal_amount=Decimal("300000.00"),
        raised_amount=Decimal("14200000.00"),
        backers_count=6920,
        start_date=date(2025, 11, 1),
        end_date=date(2025, 12, 10),
        maker_name="フレックスボックス株式会社",
        maker_url="https://example.co.jp/flexbox",
        slug="flex-box",
    ),
]


class GreenFundingScraper:
    """GreenFunding 成功案件スクレイパー（現状モック）。"""

    platform = PLATFORM

    def __init__(self, limit: int = 50) -> None:
        # 1 回の収集で取得する最大件数
        self.limit = limit

    def scrape(self) -> list[JapaneseSuccessCreate]:
        items: list[JapaneseSuccessCreate] = []
        for row in _MOCK[: self.limit]:
            raised = row["raised_amount"]
            # 成功案件（応援購入総額が下限以上）のみ採用
            if raised is None or raised < MIN_SUCCESS_JPY:
                continue
            data = {k: v for k, v in row.items() if k != "slug"}
            items.append(
                JapaneseSuccessCreate(
                    platform=self.platform,
                    source_url=f"https://greenfunding.jp/project/{row['slug']}/",
                    currency="JPY",
                    **data,
                )
            )
        return items
