"""通知プロバイダーの共通インターフェース。

各プロバイダー（Slack / メール / …）は Notifier を実装し、`send()` で
1 件のアラートを送る。プロバイダーは差し替え可能で、設定に応じて
`app.notifications.get_notifiers()` がアクティブなものだけを返す。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


class NotifierError(Exception):
    """通知の送信失敗（呼び出し側で捕捉してログ化し、処理は継続する）。"""


@dataclass
class SiteAlert:
    """1 サイト分の異常内容（通知に載せる単位）。"""

    site: str                       # サイト識別子（kickstarter 等）
    site_label: str                 # 表示名（Kickstarter 等）
    issues: list[str]               # 異常の種類（"構造変化検知" / "成功率低下"）
    error_breakdown: str            # エラー内訳の要約（"通信1 / 構造1 / 他0"）
    success_rate: float | None      # 0.0〜1.0（不明なら None）
    last_error_at: datetime | None  # 直近エラー時刻
    last_structure_error_at: datetime | None  # 直近の構造エラー時刻


@dataclass
class Alert:
    """まとめて通知する 1 件のアラート。"""

    title: str
    window: int
    admin_url: str | None
    sites: list[SiteAlert] = field(default_factory=list)


class Notifier(ABC):
    """通知プロバイダーの基底クラス。"""

    #: プロバイダー識別名（サブクラスで設定）
    name: str

    @abstractmethod
    def send(self, alert: Alert) -> None:
        """アラートを送信する。失敗時は NotifierError を送出する。"""
        raise NotImplementedError
