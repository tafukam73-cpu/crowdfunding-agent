"""Gmail のメール下書きプロバイダー。

Gmail API（users.drafts.create）で「下書き」を作成する。送信はしない。
認証は OAuth2 のリフレッシュトークン方式（バックエンド常駐向け）。

必要スコープ：https://www.googleapis.com/auth/gmail.compose

依存（遅延 import）：google-api-python-client / google-auth
  → これらは Gmail を使う場合のみ必要。未設定（mock）環境には不要。
"""
from __future__ import annotations

import base64
from email.message import EmailMessage as MimeMessage

from app.email.providers.base import (
    DraftResult,
    EmailMessage,
    EmailProvider,
    EmailProviderError,
)

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]
DRAFTS_WEB_LINK = "https://mail.google.com/mail/u/0/#drafts"


class GmailProvider(EmailProvider):
    name = "gmail"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        user: str = "me",
        sender: str | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.user = user
        self.sender = sender

    def _service(self):
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:  # 依存未導入
            raise EmailProviderError(
                "Gmail を使うには google-api-python-client / google-auth が必要です"
            ) from exc

        creds = Credentials(
            token=None,
            refresh_token=self.refresh_token,
            client_id=self.client_id,
            client_secret=self.client_secret,
            token_uri=TOKEN_URI,
            scopes=SCOPES,
        )
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def create_draft(self, message: EmailMessage) -> DraftResult:
        mime = MimeMessage()
        mime["To"] = message.to
        if message.cc:
            mime["Cc"] = ", ".join(message.cc)
        sender = message.sender or self.sender
        if sender:
            mime["From"] = sender
        mime["Subject"] = message.subject
        mime.set_content(message.body)

        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        try:
            service = self._service()
            draft = (
                service.users()
                .drafts()
                .create(userId=self.user, body={"message": {"raw": raw}})
                .execute()
            )
        except EmailProviderError:
            raise
        except Exception as exc:  # noqa: BLE001  API/認証エラー
            raise EmailProviderError(f"Gmail 下書き作成に失敗しました: {exc}") from exc

        return DraftResult(
            provider=self.name,
            draft_id=draft.get("id"),
            status="created",
            web_link=DRAFTS_WEB_LINK,
        )
