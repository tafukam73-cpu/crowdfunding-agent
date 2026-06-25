"""GreenFunding 成功案件スクレイパー（実スクレイピング・Playwright）。

トップ（discover）から公開中プロジェクト（/lab/projects/<id>）を集め、各詳細
ページを開いて JapaneseSuccessCreate へ正規化する。

GreenFunding は Makuake と構造が異なる：
  - og:meta が無い → タイトルは <title>、画像は assets の画像URLから
  - 金額は「目標¥ X」「支援総額 ¥ Y」（¥ 前置）
  - 支援人数は「支援人数 N人」
  - カテゴリは category_id 付きリンクのテキスト先頭
  - 掲載終了日は明示が無く「残り時間 N 日」表示 → 今日+N日で近似（取得不可なら null）
  - 動画は YouTube/Vimeo 埋め込み（無ければ null）
取得できない項目は null。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from html import unescape

from app.scrapers.jp_success_base import (
    JpSuccessScraper,
    find_amount_yen,
    find_count,
    find_video,
    jsonld_blocks,
)
from app.schemas.japanese_success import JapaneseSuccessCreate

PLATFORM = "greenfunding"
BASE = "https://greenfunding.jp"
DISCOVER_URL = f"{BASE}/"


def _title(html: str) -> str | None:
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    if not m:
        return None
    t = re.sub(r"\s+", " ", m.group(1)).replace("| GREENFUNDING", "")
    return unescape(t).strip() or None


def _category(html: str) -> str | None:
    """最初の category_id 付きリンクのテキストをカテゴリとして採用。"""
    for m in re.finditer(
        r'/lab/projects/search\?category_id=\d+["\'][^>]*>(.*?)</a>', html, re.S
    ):
        txt = re.sub(r"<[^>]+>", "", m.group(1)).replace("#", "").strip()
        if txt:
            return txt
    return None


def _image(html: str) -> str | None:
    """プロジェクト画像（svg アイコンは除外し、写真系拡張子を優先）。"""
    for m in re.finditer(
        r'https://assets\.greenfunding\.jp/[^"\']+\.(?:jpg|jpeg|png|webp)', html
    ):
        return m.group(0)
    return None


def _end_date(inner_text: str) -> date | None:
    """「残り時間 N 日」から掲載終了日を近似（明示が無いため）。"""
    m = re.search(r"残り時間\s*(\d+)\s*日", inner_text)
    if not m:
        return None
    return date.today() + timedelta(days=int(m.group(1)))


class GreenFundingScraper(JpSuccessScraper):
    platform = PLATFORM

    def discover_urls(self) -> list[str]:
        html = self._client.get_text(DISCOVER_URL)
        ids = re.findall(r"/lab/projects/(\d+)", html)
        seen: list[str] = []
        for i in ids:
            if i not in seen:
                seen.append(i)
        return [f"{BASE}/lab/projects/{i}" for i in seen]

    def parse_detail(
        self, url: str, inner_text: str, html: str
    ) -> JapaneseSuccessCreate | None:
        title = _title(html)
        if not title:
            return None

        # 説明は JSON-LD(Product) があれば採用（無ければ null）
        product = next(
            (b for b in jsonld_blocks(html) if b.get("@type") == "Product"), {}
        )
        description = product.get("description")
        description = unescape(description) if description else None
        image_url = product.get("image") or _image(html)

        return JapaneseSuccessCreate(
            platform=self.platform,
            title=title[:500],
            source_url=url,
            category=_category(html),
            description=description,
            image_url=image_url,
            video_url=find_video(html),
            currency="JPY",
            goal_amount=find_amount_yen(inner_text, "目標"),
            raised_amount=find_amount_yen(inner_text, "支援総額"),
            backers_count=find_count(inner_text, "支援人数", "人"),
            start_date=None,  # ページに明示が無い
            end_date=_end_date(inner_text),
            maker_name=None,  # 詳細ページで安定取得できないため null
            maker_url=None,
        )
