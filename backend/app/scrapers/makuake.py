"""Makuake 成功案件スクレイパー（実スクレイピング・Playwright）。

discover ページから公開中プロジェクトを集め、各詳細ページを開いて
JapaneseSuccessCreate へ正規化する。応援購入総額が下限以上の案件を
「成功事例」として比較用に蓄える。

取得方針：
  - タイトル/説明/画像：JSON-LD(Product) と og:meta から
  - カテゴリ：プロジェクトページのタグ（#ガジェット 等）の先頭
  - 応援購入総額/目標金額/サポーター数/終了日：本文テキストから
  - 動画：YouTube/Vimeo 埋め込み or og:video（無ければ null）
  - 開始日/メーカー公式URL：ページに安定して存在しないため基本 null
取得できない項目は null。
"""
from __future__ import annotations

import re
from html import unescape

from app.scrapers.jp_success_base import (
    JpSuccessScraper,
    find_amount,
    find_count,
    find_date_after,
    find_video,
    jsonld_blocks,
    meta_content,
)
from app.schemas.japanese_success import JapaneseSuccessCreate

PLATFORM = "makuake"
BASE = "https://www.makuake.com"
DISCOVER_URL = f"{BASE}/discover/"


def _clean_title(raw: str | None) -> str | None:
    if not raw:
        return None
    # og:title は "Makuake｜<本題>｜Makuake（マクアケ）" 形式
    t = raw.replace("｜Makuake（マクアケ）", "").replace("Makuake｜", "")
    return t.strip() or None


def _first_tag_category(html: str) -> str | None:
    """最初の #タグ をカテゴリとして採用（海外案件のカテゴリと整合しやすい）。"""
    for m in re.finditer(r'/discover/tags/\d+/[^"\']*["\'][^>]*>(.*?)</a>', html, re.S):
        txt = re.sub(r"<[^>]+>", "", m.group(1))
        txt = txt.replace("#", "").strip()
        if txt:
            return txt
    return None


def _maker_name(inner_text: str) -> str | None:
    """「実行者」付近の名称を best-effort で取得（取れなければ null）。"""
    m = re.search(r"実行者\s*[:：]?\s*\n?\s*([^\n]{2,40})", inner_text)
    if not m:
        return None
    cand = m.group(1).strip()
    # UI ラベルや誘導文を除外
    if not cand or "お問い合わせ" in cand or "フォロー" in cand:
        return None
    return cand


class MakuakeScraper(JpSuccessScraper):
    platform = PLATFORM

    def discover_urls(self) -> list[str]:
        html = self._client.get_text(DISCOVER_URL)
        slugs = re.findall(
            r'href=["\'](?:https://www\.makuake\.com)?/project/([^"\'/?#]+)', html
        )
        seen: list[str] = []
        for s in slugs:
            if s not in seen:
                seen.append(s)
        return [f"{BASE}/project/{s}/" for s in seen]

    def parse_detail(
        self, url: str, inner_text: str, html: str
    ) -> JapaneseSuccessCreate | None:
        ld = next(
            (b for b in jsonld_blocks(html) if b.get("@type") == "Product"), {}
        )

        title = ld.get("name") or _clean_title(meta_content(html, "og:title"))
        if not title:
            return None
        title = unescape(title)

        description = meta_content(html, "og:description") or ld.get("description")
        description = unescape(description) if description else None
        image_url = ld.get("image") or meta_content(html, "og:image")

        return JapaneseSuccessCreate(
            platform=self.platform,
            title=title[:500],
            source_url=url,
            category=_first_tag_category(html),
            description=description,
            image_url=image_url,
            video_url=find_video(html),
            currency="JPY",
            goal_amount=find_amount(inner_text, "目標金額"),
            raised_amount=find_amount(inner_text, "応援購入総額"),
            backers_count=find_count(inner_text, "サポーター", "人"),
            start_date=None,  # ページに安定して存在しない
            end_date=find_date_after(inner_text, "終了日"),
            maker_name=_maker_name(inner_text),
            maker_url=None,
        )
