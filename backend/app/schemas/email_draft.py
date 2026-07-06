"""営業メール下書き API のスキーマ。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.ai.prompts import DEFAULT_TONE, EmailTone
from app.models.email_draft import EmailType


class EmailDraftOut(BaseModel):
    id: int
    project_id: int
    email_type: EmailType
    subject: str
    body: str
    language: str
    model: str
    # 営業メール品質向上で追加（後方互換のため任意）
    subject_options: list[str] | None = None
    selected_subject: str | None = None
    tone: str | None = None
    japanese_summary: str | None = None
    # パーソナライズ材料（後方互換のため任意）
    personalization_context: dict | None = None
    personalized_compliment: str | None = None
    product_highlights: list[str] | None = None
    provider: str | None = None
    provider_draft_id: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GenerateDraftsRequest(BaseModel):
    """メール生成リクエスト。tone 未指定なら professional。"""

    tone: EmailTone = DEFAULT_TONE


class SelectSubjectRequest(BaseModel):
    """件名選択リクエスト。"""

    selected_subject: str


class ProviderDraftRequest(BaseModel):
    """プロバイダー下書き作成リクエスト。to 未指定なら宛先を自動解決。"""

    to: str | None = None


class FollowupEmailRequest(BaseModel):
    """フォローアップメール作成リクエスト。"""

    # 経過日数を明示指定（テスト・手動用）。未指定なら最終営業日から自動算出。
    days: int | None = None
    # True のとき作成後に営業状況を「返信待ち」に更新する（既定 True）。
    set_awaiting_reply: bool = True
    # 宛先メール（未指定なら担当者/連絡先から自動解決）。
    to: str | None = None


class FollowupEmailResult(BaseModel):
    """フォローアップメール作成の結果。"""

    draft: EmailDraftOut
    stage: str                       # light / repropose / final
    stage_label: str                 # 軽い確認 / 再提案 / 最終フォロー
    days_since_last_outreach: int
    follow_up_level: str             # normal / high / final
    gmail_compose_url: str           # Gmail 下書きを開く URL（宛先/件名/本文入り）
    recipient: str | None = None
    sales_status: str


class ProviderDraftResult(BaseModel):
    """プロバイダー下書き作成結果。"""

    provider: str
    draft_id: str | None
    status: str
    to: str
    web_link: str | None = None
    detail: str | None = None


class EmailProviderInfo(BaseModel):
    """現在有効なメールプロバイダー情報。"""

    provider: str
    gmail_configured: bool
