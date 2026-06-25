"""Makuake 成功案件スクレイパー。

日本クラファン（Makuake）の応援購入成功案件を収集し、JapaneseSuccessCreate の
リストを返す。海外案件の収集（projects）とは別系統で、営業時の「日本での類似成功
事例」比較に使う比較用データを蓄える。

現状はモック実装（実サイトへはアクセスしない）。BaseScraper と同じく
`scrape()` で正規化済みデータを返すインターフェースに揃えてあるため、後から
Playwright 等による実スクレイパーへ差し替えやすい。

差し替え時の想定フロー：
  1. 一覧（カテゴリ別ランキング等）を取得
  2. 各案件詳細をパース
  3. 応援購入総額が MIN_SUCCESS_JPY 以上のものだけ成功案件として採用
  4. JapaneseSuccessCreate へ正規化
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.japanese_success import MIN_SUCCESS_JPY
from app.schemas.japanese_success import JapaneseSuccessCreate

PLATFORM = "makuake"

# Makuake の成功案件モックデータ。
# カテゴリは海外案件側（projects）と揃えてあり、類似事例マッチングが効くようにしている。
_MOCK: list[dict] = [
    dict(
        title="どこでも充電できる折りたたみソーラーパネル「SOLA-FOLD」",
        category="ガジェット",
        description="アウトドアや防災に。軽量で持ち運べる高効率ソーラーパネル。",
        image_url="https://picsum.photos/seed/jp-solar/640/360",
        video_url="https://www.makuake.com/project/sola-fold/",
        goal_amount=Decimal("1000000.00"),
        raised_amount=Decimal("38500000.00"),
        backers_count=2980,
        start_date=date(2025, 9, 1),
        end_date=date(2025, 10, 31),
        maker_name="ソラフォールド株式会社",
        maker_url="https://example.co.jp/solafold",
        slug="sola-fold",
    ),
    dict(
        title="AIが空間を解析する全自動ノイキャンイヤホン「QUIET-AI」",
        category="オーディオ",
        description="周囲の騒音をAIがリアルタイム解析し最適なノイズキャンセリングを実現。",
        image_url="https://picsum.photos/seed/jp-earbuds/640/360",
        video_url="https://www.makuake.com/project/quiet-ai/",
        goal_amount=Decimal("2000000.00"),
        raised_amount=Decimal("96200000.00"),
        backers_count=11240,
        start_date=date(2025, 7, 10),
        end_date=date(2025, 8, 31),
        maker_name="クワイエット株式会社",
        maker_url="https://example.co.jp/quiet",
        slug="quiet-ai",
    ),
    dict(
        title="財布に入る18機能チタンマルチツール「TITAN-CARD」",
        category="アウトドア",
        description="厚さ2mm。カードサイズに必要な機能を凝縮したEDCツール。",
        image_url="https://picsum.photos/seed/jp-titanium/640/360",
        video_url=None,
        goal_amount=Decimal("500000.00"),
        raised_amount=Decimal("21800000.00"),
        backers_count=6730,
        start_date=date(2025, 10, 1),
        end_date=date(2025, 11, 20),
        maker_name="エッジワークス合同会社",
        maker_url="https://example.co.jp/edgeworks",
        slug="titan-card",
    ),
    dict(
        title="アプリで全自動管理する卓上水耕栽培キット「GROW-BOX」",
        category="ホーム",
        description="水やり・照明・温度をアプリで自動管理。初心者でも野菜が育つ。",
        image_url="https://picsum.photos/seed/jp-garden/640/360",
        video_url="https://www.makuake.com/project/grow-box/",
        goal_amount=Decimal("1500000.00"),
        raised_amount=Decimal("28900000.00"),
        backers_count=3410,
        start_date=date(2025, 5, 1),
        end_date=date(2025, 6, 30),
        maker_name="グリーンラボ株式会社",
        maker_url="https://example.co.jp/greenlab",
        slug="grow-box",
    ),
    dict(
        title="磁力で宙に浮く回転式デザイン時計「LEVI-CLOCK」",
        category="インテリア",
        description="磁気浮上で静かに回転。近未来的なデザインの卓上時計。",
        image_url="https://picsum.photos/seed/jp-clock/640/360",
        video_url=None,
        goal_amount=Decimal("800000.00"),
        raised_amount=Decimal("12400000.00"),
        backers_count=1520,
        start_date=date(2025, 8, 15),
        end_date=date(2025, 9, 30),
        maker_name="レビデザイン株式会社",
        maker_url="https://example.co.jp/levidesign",
        slug="levi-clock",
    ),
    dict(
        title="3秒で乾く超吸水マイクロファイバータオル「DRY-FAST」",
        category="ホーム",
        description="独自繊維で吸水・速乾を両立。旅行やジムに最適な軽量タオル。",
        image_url="https://picsum.photos/seed/jp-towel/640/360",
        video_url="https://www.makuake.com/project/dry-fast/",
        goal_amount=Decimal("300000.00"),
        raised_amount=Decimal("7600000.00"),
        backers_count=4120,
        start_date=date(2025, 11, 1),
        end_date=date(2025, 12, 15),
        maker_name="ドライファスト株式会社",
        maker_url="https://example.co.jp/dryfast",
        slug="dry-fast",
    ),
]


class MakuakeScraper:
    """Makuake 成功案件スクレイパー（現状モック）。"""

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
                    source_url=f"https://www.makuake.com/project/{row['slug']}/",
                    currency="JPY",
                    **data,
                )
            )
        return items
