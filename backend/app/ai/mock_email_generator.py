"""モック営業メール生成器。

外部 API を使わず、案件情報を差し込んだ英文の下書きを 3 種別生成する
（営業先は海外メーカー想定のため英語）。Claude 実装までの画面・DB・API
構造の検証用。自動送信はしない。

本文は要件の要素（商品名・魅力・実績への敬意・日本市場の可能性・
Makuake/GreenFunding 展開・独占販売権・オンラインミーティング）を自然に含む。
署名は AI ではなく prompts.append_signature で固定テンプレートを末尾連結する
（メール設定が未登録でも .env フォールバックで動く）。
"""
from __future__ import annotations

from app.ai.email_generator import EmailDraftResult, EmailGenerator
from app.ai.prompts import SenderContext, append_signature, trim_company_profile
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


def _intro(ctx: SenderContext) -> str:
    """会社紹介の一文（会社紹介文があれば短く引用、なければ既定文）。"""
    profile = trim_company_profile(ctx.company_profile, max_chars=200)
    who = ctx.company_name or "a Japanese distribution team"
    if profile:
        return f"I'm with {who}. {profile}"
    return (
        f"I'm with {who} that helps overseas products launch on Japan's leading "
        "crowdfunding platforms (Makuake and GreenFunding)."
    )


class MockEmailGenerator(EmailGenerator):
    name = "mock-email-v1"

    def generate(
        self, project: Project, ctx: SenderContext | None = None
    ) -> list[EmailDraftResult]:
        ctx = ctx or SenderContext.fallback()
        title = project.title
        maker = _maker(project)
        platform = _platform(project)
        intro = _intro(ctx)

        drafts: list[EmailDraftResult] = []

        # 1) 初回営業
        drafts.append(
            EmailDraftResult(
                email_type=EmailType.initial_outreach,
                subject=f"Bringing {title} to the Japanese market",
                body=append_signature(
                    f"Hello {maker},\n\n"
                    f"I recently came across {title} on {platform} and was genuinely "
                    "impressed — both by the product itself and by the strong support "
                    "it earned from your backers. Congratulations on that success.\n\n"
                    f"{intro}\n\n"
                    f"I believe {title} has real potential with Japanese backers, who "
                    "tend to embrace well-designed, original products like yours. We "
                    "would love to introduce it to the Japanese market through a "
                    "Makuake or GreenFunding launch, and ideally explore exclusive "
                    "distribution rights for Japan together.\n\n"
                    "Would you be open to a short online meeting next week to discuss?",
                    ctx,
                ),
                model=self.name,
            )
        )

        # 2) 独占販売権打診
        drafts.append(
            EmailDraftResult(
                email_type=EmailType.exclusive_rights,
                subject=f"Exclusive Japan distribution for {title}",
                body=append_signature(
                    f"Hello {maker},\n\n"
                    f"Following up on {title} — the results you achieved on {platform} "
                    "speak for themselves, and I'd love to help replicate that success "
                    "in Japan.\n\n"
                    "I'd like to propose an exclusive distribution partnership for the "
                    "Japanese market. With exclusivity, we can commit the marketing "
                    "budget and operational resources needed to maximize your launch on "
                    "Makuake or GreenFunding, handle Japanese customer support, and "
                    "manage import and certification (e.g., PSE) where required.\n\n"
                    "Could we set up a short online meeting to discuss terms, timing, "
                    "and expected volumes?",
                    ctx,
                ),
                model=self.name,
            )
        )

        # 3) フォローアップ
        drafts.append(
            EmailDraftResult(
                email_type=EmailType.followup,
                subject=f"Re: {title} — quick follow-up",
                body=append_signature(
                    f"Hello {maker},\n\n"
                    "I wanted to gently follow up on my previous message about bringing "
                    f"{title} to the Japanese market. I understand you're busy, so even "
                    "a brief reply would be appreciated.\n\n"
                    "If it helps, I'm happy to share a short overview of past launches "
                    "we've supported on Makuake and GreenFunding, and how exclusive "
                    "distribution could work for Japan. A 20-minute online meeting at "
                    "your convenience would be more than enough.\n\n"
                    "Looking forward to hearing from you.",
                    ctx,
                ),
                model=self.name,
            )
        )

        return drafts
