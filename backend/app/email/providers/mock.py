"""モックのメール下書きプロバイダー（未設定時の既定）。

実際のメールサービスには接続せず、作成された体の結果を返す。
画面・API・DB の動作確認に使う。
"""
from __future__ import annotations

import uuid

from app.email.providers.base import DraftResult, EmailMessage, EmailProvider


class MockEmailProvider(EmailProvider):
    name = "mock"

    def create_draft(self, message: EmailMessage) -> DraftResult:
        return DraftResult(
            provider=self.name,
            draft_id=f"mock-{uuid.uuid4().hex[:12]}",
            status="created",
            web_link=None,
            detail="モックプロバイダー：実際のメールサービスには作成していません。",
        )
