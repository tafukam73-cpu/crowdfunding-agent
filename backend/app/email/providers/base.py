"""メール下書きプロバイダーの共通インターフェース。

各プロバイダー（Gmail / Outlook / mock …）は EmailProvider を実装し、
`create_draft()` で「下書き」を作成する。送信は行わない（送信は利用者が
各メールサービス上で最終確認して実施する運用）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class EmailProviderError(Exception):
    """プロバイダー側での下書き作成失敗。"""


@dataclass
class EmailMessage:
    """下書きにするメール内容。取得できない項目は呼び出し側で補完する。"""

    to: str
    subject: str
    body: str
    cc: list[str] | None = None
    sender: str | None = None  # From（省略時はプロバイダー既定）


@dataclass
class DraftResult:
    """下書き作成結果。"""

    provider: str            # "gmail" / "mock" / ...
    draft_id: str | None     # プロバイダー側の下書きID
    status: str              # "created"
    web_link: str | None = None   # 下書きを開く URL（あれば）
    detail: str | None = None


class EmailProvider(ABC):
    """メール下書きプロバイダーの基底クラス。"""

    #: プロバイダー識別名（サブクラスで設定）
    name: str

    @abstractmethod
    def create_draft(self, message: EmailMessage) -> DraftResult:
        """下書きを作成し結果を返す。失敗時は EmailProviderError を送出する。"""
        raise NotImplementedError
