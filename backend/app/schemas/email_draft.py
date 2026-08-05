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


class QualificationAudit(BaseModel):
    """営業対象除外判定の監査情報（送信前関門）。

    **安全な値だけを載せる。** 数値 confidence / score / probability / forecast /
    返信率 / 成功率 / makuake_fit / japan_crowdfunding_score / internal_db の URL /
    メールアドレス / Evidence 本文は含めない。
    """

    stage: str
    # 判定できなかった場合は None（observe でも隠さずそのまま出す）。
    decision: str | None = None
    machine_decision: str | None = None
    effective_decision: str | None = None
    overridden: bool = False
    blocker_codes: list[str] = []
    review_codes: list[str] = []
    reasons: list[str] = []
    checked_at: str | None = None
    persisted: bool = False


class ProviderDraftResult(BaseModel):
    """プロバイダー下書き作成結果。"""

    provider: str
    draft_id: str | None
    status: str
    to: str
    web_link: str | None = None
    detail: str | None = None
    # 送信前関門の判定。observe モードで不合格のまま作成した場合の警告に使う
    # （enforce では不合格なら 409 になるため 200 に載ることはない）。
    qualification: QualificationAudit | None = None


class EmailProviderInfo(BaseModel):
    """現在有効なメールプロバイダー情報。"""

    provider: str
    gmail_configured: bool
    # 送信前関門の適用モード（observe / enforce）。運用者が現在の挙動を確認する
    # ためのグローバル設定。案件単位のレスポンスには載せない（重複させない）。
    outreach_gate_mode: str = "observe"
