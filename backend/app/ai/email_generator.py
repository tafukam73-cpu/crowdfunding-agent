"""営業メール下書き生成の共通インターフェース。

モック生成器・Claude 生成器はこの EmailGenerator を実装する。
出力は EmailDraftResult（DB/モデル非依存）。自動送信は一切しない。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.ai.prompts import DEFAULT_TONE, EmailTone, SenderContext
from app.models.email_draft import EmailType
from app.models.project import Project

# 生成する下書きの種別（順序＝表示順）
EMAIL_TYPES: list[EmailType] = [
    EmailType.initial_outreach,   # 初回営業
    EmailType.exclusive_rights,   # 独占販売権打診
    EmailType.followup,           # フォローアップ
]

EMAIL_TYPE_LABELS: dict[EmailType, str] = {
    EmailType.initial_outreach: "初回営業",
    EmailType.exclusive_rights: "独占販売権打診",
    EmailType.followup: "フォローアップ",
}


class EmailDraftResult(BaseModel):
    email_type: EmailType
    # 後方互換：subject は「実際に採用された件名」（既定は候補の1つ目）
    subject: str
    body: str
    language: str = "en"
    model: str
    # 件名候補（3案）。selected_subject は初期選択（既定で候補の先頭）。
    subject_options: list[str] = Field(default_factory=list)
    selected_subject: str = ""
    tone: str = DEFAULT_TONE.value
    japanese_summary: str = ""
    # パーソナライズ材料（商品・メーカーごとの個別化）
    personalization_context: dict | None = None
    personalized_compliment: str = ""
    product_highlights: list[str] = Field(default_factory=list)


class EmailGenerator(ABC):
    """全メール生成器の基底クラス。"""

    name: str = "base"
    #: 直近呼び出しのトークン使用量（モックは None）
    last_usage: dict | None = None

    @abstractmethod
    def generate(
        self,
        project: Project,
        ctx: SenderContext | None = None,
        tone: EmailTone = DEFAULT_TONE,
    ) -> list[EmailDraftResult]:
        """案件に対し 3 種別の下書きを生成して返す。

        ctx は差出人/会社情報（メール設定）。None の場合は .env フォールバックを
        使い、設定未登録でも生成が動くようにする。本文末尾には署名を連結する。
        tone は文章のトーン（既定は professional）。各下書きは件名候補 3 案と
        日本語要約を含む。
        """
        raise NotImplementedError
