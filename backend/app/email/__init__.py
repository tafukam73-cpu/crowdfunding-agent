"""メール下書きプロバイダーのファクトリ。

未設定なら mock、Gmail の認証情報が揃っていれば Gmail を使う。
プロバイダーは EmailProvider インターフェースに準拠するため、将来 Outlook 等を
`providers/outlook.py` として追加し、ここで分岐するだけで差し替えられる。
"""
from __future__ import annotations

from app.config import settings
from app.email.providers.base import EmailMessage, EmailProvider, EmailProviderError


def is_gmail_configured() -> bool:
    return bool(
        settings.gmail_client_id
        and settings.gmail_client_secret
        and settings.gmail_refresh_token
    )


def active_provider_name() -> str:
    return "gmail" if is_gmail_configured() else "mock"


def get_email_provider() -> EmailProvider:
    """設定に応じたメール下書きプロバイダーを返す。"""
    if is_gmail_configured():
        # 遅延 import：google ライブラリ未導入でも mock 経路は動く
        from app.email.providers.gmail import GmailProvider

        return GmailProvider(
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            refresh_token=settings.gmail_refresh_token,
            user=settings.gmail_user or "me",
            sender=settings.gmail_sender or None,
        )

    from app.email.providers.mock import MockEmailProvider

    return MockEmailProvider()


__all__ = [
    "EmailMessage",
    "EmailProvider",
    "EmailProviderError",
    "get_email_provider",
    "is_gmail_configured",
    "active_provider_name",
]
