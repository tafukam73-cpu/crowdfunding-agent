"""通知プロバイダーのファクトリ。

設定に応じてアクティブな通知プロバイダーを返す。何も設定されていなければ
空リストを返し、呼び出し側は「何もしない」。将来 `providers` を増やす場合は
ここで分岐を足すだけでよい（例：メール通知）。

メール通知の追加例：
    if settings.alert_email_to:
        from app.notifications.email import EmailNotifier
        notifiers.append(EmailNotifier(...))
"""
from __future__ import annotations

from app.config import settings
from app.notifications.base import (
    Alert,
    Notifier,
    NotifierError,
    SiteAlert,
)


def get_notifiers() -> list[Notifier]:
    """設定済みの通知プロバイダー一覧を返す（未設定なら空リスト）。"""
    notifiers: list[Notifier] = []

    if settings.slack_webhook_url:
        from app.notifications.slack import SlackNotifier

        notifiers.append(SlackNotifier(settings.slack_webhook_url))

    # 将来：メール等の通知プロバイダーをここに追加する

    return notifiers


__all__ = [
    "Alert",
    "Notifier",
    "NotifierError",
    "SiteAlert",
    "get_notifiers",
]
