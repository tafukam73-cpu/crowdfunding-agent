"""モック営業メール生成器。

外部 API を使わず、案件情報を差し込んだ英文の下書きを 3 種別生成する
（営業先は海外メーカー想定のため英語）。Claude 実装までの画面・DB・API
構造の検証用。自動送信はしない。
"""
from __future__ import annotations

from app.ai.email_generator import EmailDraftResult, EmailGenerator
from app.config import settings
from app.models.email_draft import EmailType
from app.models.project import Project


def _maker(project: Project) -> str:
    return project.maker_name or "there"


def _platform(project: Project) -> str:
    return {
        "kickstarter": "Kickstarter",
        "indiegogo": "Indiegogo",
        "wadiz": "Wadiz",
        "makuake": "Makuake",
        "greenfunding": "GreenFunding",
    }.get(project.source_site, "your crowdfunding campaign")


def _signature() -> str:
    # 差出人は設定（.env）から
    return f"\n\nBest regards,\n{settings.sender_name}\n{settings.sender_company}\n"


class MockEmailGenerator(EmailGenerator):
    name = "mock-email-v1"

    def generate(self, project: Project) -> list[EmailDraftResult]:
        title = project.title
        maker = _maker(project)
        platform = _platform(project)

        drafts: list[EmailDraftResult] = []

        # 1) 初回営業
        drafts.append(
            EmailDraftResult(
                email_type=EmailType.initial_outreach,
                subject=f"Bringing {title} to the Japanese market",
                body=(
                    f"Hello {maker},\n\n"
                    f"I came across {title} on {platform} and was very impressed. "
                    "I work with a Japanese distribution team that helps overseas "
                    "products launch on Japan's leading crowdfunding platforms "
                    "(Makuake and GreenFunding).\n\n"
                    "We believe your product has strong potential with Japanese "
                    "backers. We would love to discuss how we could support a Japan "
                    "launch, including localization, logistics, and marketing.\n\n"
                    "Would you be open to a short call next week?"
                    + _signature()
                ),
                model=self.name,
            )
        )

        # 2) 独占販売権打診
        drafts.append(
            EmailDraftResult(
                email_type=EmailType.exclusive_rights,
                subject=f"Exclusive Japan distribution for {title}",
                body=(
                    f"Hello {maker},\n\n"
                    f"Following up on {title}, we would like to propose an exclusive "
                    "distribution partnership for the Japanese market.\n\n"
                    "With exclusivity, we can commit the marketing budget and "
                    "operational resources needed to maximize your launch on Makuake "
                    "or GreenFunding, handle customer support in Japanese, and manage "
                    "import and certification (e.g., PSE / technical conformity) where "
                    "required.\n\n"
                    "Could we set up a call to discuss terms, target timing, and "
                    "expected volumes?"
                    + _signature()
                ),
                model=self.name,
            )
        )

        # 3) フォローアップ
        drafts.append(
            EmailDraftResult(
                email_type=EmailType.followup,
                subject=f"Re: {title} — quick follow-up",
                body=(
                    f"Hello {maker},\n\n"
                    "I wanted to gently follow up on my previous message regarding a "
                    f"Japan launch for {title}. I understand you are busy, so even a "
                    "brief reply would be appreciated.\n\n"
                    "If helpful, I can share a short overview of past launches we have "
                    "supported and the typical results on Japanese crowdfunding "
                    "platforms.\n\n"
                    "Looking forward to hearing from you."
                    + _signature()
                ),
                model=self.name,
            )
        )

        return drafts
