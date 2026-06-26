"""Slack Incoming Webhook 通知プロバイダー。

Webhook URL に Block Kit 形式のメッセージを POST する。送信失敗時は
NotifierError を送出する（呼び出し側がログ化し、ジョブは止めない）。
"""
from __future__ import annotations

import logging

import httpx

from app.notifications.base import Alert, Notifier, NotifierError, SiteAlert

logger = logging.getLogger("notifications.slack")


def _fmt_rate(rate: float | None) -> str:
    return "—" if rate is None else f"{round(rate * 100)}%"


def _fmt_dt(dt) -> str:
    return "—" if dt is None else dt.strftime("%Y-%m-%d %H:%M")


def _site_lines(s: SiteAlert) -> str:
    """1 サイト分の本文（Slack mrkdwn）。"""
    parts = [
        f"*{s.site_label}*  {' / '.join(s.issues)}",
        f"> 成功率: {_fmt_rate(s.success_rate)}",
        f"> エラー内訳: {s.error_breakdown}",
    ]
    if s.last_structure_error_at is not None:
        parts.append(f"> 直近の構造エラー: {_fmt_dt(s.last_structure_error_at)}")
    if s.last_error_at is not None:
        parts.append(f"> 直近エラー: {_fmt_dt(s.last_error_at)}")
    return "\n".join(parts)


class SlackNotifier(Notifier):
    name = "slack"

    def __init__(self, webhook_url: str, *, timeout: float = 10.0) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def _build_payload(self, alert: Alert) -> dict:
        blocks: list[dict] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"⚠ {alert.title}"},
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"対象サイト {len(alert.sites)} 件 ・ 直近 {alert.window} 実行を集計",
                    }
                ],
            },
            {"type": "divider"},
        ]
        for s in alert.sites:
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": _site_lines(s)}}
            )
        if alert.admin_url:
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "管理画面を開く"},
                            "url": alert.admin_url,
                        }
                    ],
                }
            )
        # fallback text（通知欄・block 非対応クライアント用）
        summary = "、".join(f"{s.site_label}（{'/'.join(s.issues)}）" for s in alert.sites)
        return {"text": f"⚠ {alert.title}: {summary}", "blocks": blocks}

    def send(self, alert: Alert) -> None:
        payload = self._build_payload(alert)
        try:
            resp = httpx.post(self.webhook_url, json=payload, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise NotifierError(f"Slack 送信に失敗: {exc}") from exc
        if resp.status_code >= 300:
            raise NotifierError(
                f"Slack 送信が失敗ステータス {resp.status_code}: {resp.text[:200]}"
            )
        logger.info("slack alert sent (%d sites)", len(alert.sites))
