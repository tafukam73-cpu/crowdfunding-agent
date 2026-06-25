"""日本販売状況の検索プロバイダー共通インターフェース。

各サイトのプロバイダーは SearchProvider を実装し、`search(query)` で
ヒット候補（SearchHit）を返す。サイト固有の検索方法（API/スクレイピング/モック）は
各実装に閉じ込め、判定ロジック（availability_service）はサイトに依存しない。
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.availability import AvailabilitySite


@dataclass
class SearchHit:
    """検索ヒット（判定根拠の 1 件）。"""

    site: str
    title: str | None
    url: str | None
    match_score: int  # 商品名の一致スコア 0〜100


class SearchProvider(ABC):
    """サイト別検索プロバイダーの基底クラス。"""

    site: AvailabilitySite

    @abstractmethod
    def search(self, query: str) -> list[SearchHit]:
        """クエリで検索し、ヒット候補を返す（無ければ空リスト）。"""
        raise NotImplementedError


_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]+")


def score_match(query: str, title: str | None) -> int:
    """クエリと商品名の一致スコア（0〜100）。実プロバイダー用の簡易類似度。

    英数トークンの一致率（Jaccard 近似）。日本語の表記揺れには弱いため、
    将来 AI 判定や正規化で補強する前提。
    """
    if not title:
        return 0
    q = {t.lower() for t in _TOKEN_RE.findall(query)}
    t = {t.lower() for t in _TOKEN_RE.findall(title)}
    if not q or not t:
        return 0
    inter = len(q & t)
    union = len(q | t)
    return int(round(inter / union * 100)) if union else 0
