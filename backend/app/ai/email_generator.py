"""営業メール下書き生成の共通インターフェース。

モック生成器・Claude 生成器はこの EmailGenerator を実装する。
出力は EmailDraftResult（DB/モデル非依存）。自動送信は一切しない。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.ai.prompts import SenderContext
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
    subject: str
    body: str
    language: str = "en"
    model: str


class EmailGenerator(ABC):
    """全メール生成器の基底クラス。"""

    name: str = "base"
    #: 直近呼び出しのトークン使用量（モックは None）
    last_usage: dict | None = None

    @abstractmethod
    def generate(
        self, project: Project, ctx: SenderContext | None = None
    ) -> list[EmailDraftResult]:
        """案件に対し 3 種別の下書きを生成して返す。

        ctx は差出人/会社情報（メール設定）。None の場合は .env フォールバックを
        使い、設定未登録でも生成が動くようにする。本文末尾には署名を連結する。
        """
        raise NotImplementedError
