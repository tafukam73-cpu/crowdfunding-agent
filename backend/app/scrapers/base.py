"""スクレイパー共通基盤。

各サイトのスクレイパーは BaseScraper を継承し、`scrape()` で
正規化済みの ProjectCreate のリストを返す。サイト固有の取得・パース処理は
各サブクラス内に閉じ込め、後段（collector）はサイトに依存しない。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.project import SourceSite
from app.schemas.project import ProjectCreate


class BaseScraper(ABC):
    """全スクレイパーの基底クラス。"""

    #: 対象サイト（サブクラスで指定）
    site: SourceSite

    def __init__(self, limit: int = 20) -> None:
        # 1 回の収集で取得する最大件数
        self.limit = limit

    @abstractmethod
    def scrape(self) -> list[ProjectCreate]:
        """案件を取得し、正規化済みの ProjectCreate リストを返す。

        サブクラスは以下の流れで実装する想定：
          1. 一覧ページ/API を取得（fetch）
          2. 各案件をパース（parse）
          3. ProjectCreate へ正規化（normalize）

        例外が発生した場合はそのまま送出してよい（collector が捕捉し
        scrape_runs にエラーとして記録する）。
        """
        raise NotImplementedError
