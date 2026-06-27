"""モック営業メール生成器。

外部 API を使わず、案件情報を差し込んだ英文の下書きを 3 種別生成する
（営業先は海外メーカー想定のため英語）。Claude 実装までの・Claude 未設定時の
画面・DB・API 構造の検証用。自動送信はしない。

各下書きは以下を含む：
- 件名候補 3 案（subject_options）と初期選択（selected_subject）
- 本文（要件の要素：商品名・魅力・興味を持った理由・クラファン実績への敬意・
  日本市場の可能性・Makuake/GreenFunding 展開・独占販売権・オンラインミーティング）
- 日本語要約（japanese_summary）
- トーン（professional / friendly / executive / short / detailed）の反映

署名は AI ではなく prompts.append_signature で固定テンプレートを末尾連結する
（メール設定が未登録でも .env フォールバックで動く）。
"""
from __future__ import annotations

from app.ai.email_generator import EmailDraftResult, EmailGenerator
from app.ai.personalization import build_personalization
from app.ai.prompts import (
    DEFAULT_TONE,
    EmailTone,
    SenderContext,
    append_signature,
    trim_company_profile,
)
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


def _greeting(maker: str, tone: EmailTone) -> str:
    if tone is EmailTone.friendly:
        return f"Hi {maker},"
    if tone is EmailTone.executive:
        return f"Dear {maker},"
    return f"Hello {maker},"


# 件名候補（3 案）を種別ごとに用意する。
def _subject_options(email_type: EmailType, title: str) -> list[str]:
    if email_type is EmailType.initial_outreach:
        return [
            f"Partnership Opportunity in Japan for {title}",
            f"Introducing {title} to the Japanese Market",
            f"Potential Exclusive Distribution in Japan for {title}",
        ]
    if email_type is EmailType.exclusive_rights:
        return [
            f"Exclusive Japan Distribution for {title}",
            f"Partnering on {title} for the Japanese Market",
            f"Bringing {title} to Japan — Exclusive Proposal",
        ]
    # followup
    return [
        f"Re: {title} — quick follow-up",
        f"Following up: bringing {title} to Japan",
        f"Still keen to introduce {title} in Japan",
    ]


def _parts(
    project: Project, email_type: EmailType, ctx: SenderContext, p: dict
) -> dict[str, str]:
    """本文の構成パーツ（トーンで取捨選択する）。

    p は personalization.build_personalization() の出力。商品・メーカーごとに
    変わる称賛文・訴求文を本文へ自然に織り込む。
    """
    title = project.title
    intro = _intro(ctx)
    opening = p["recommended_opening_sentence"]
    compliment = p["personalized_compliment"]
    japan_angle = p["japan_market_angle"]
    maker_appeal = p["maker_appeal"]

    if email_type is EmailType.initial_outreach:
        return {
            "open": f"{opening} {compliment}",
            "intro": intro,
            "maker": maker_appeal,
            "value": (
                f"{japan_angle} We would love to introduce {title} to Japan through a "
                "Makuake or GreenFunding launch."
            ),
            "exclusive": (
                "Ideally, we'd also like to explore exclusive distribution rights "
                "for the Japanese market together."
            ),
            "cta": (
                "Would you be open to a short online meeting next week to discuss?"
            ),
        }
    if email_type is EmailType.exclusive_rights:
        return {
            "open": (
                f"Following up on {title} — {compliment} The results you achieved "
                "speak for themselves, and I'd love to help replicate that success "
                "in Japan."
            ),
            "intro": intro,
            "maker": maker_appeal,
            "value": (
                "I'd like to propose an exclusive distribution partnership for the "
                f"Japanese market. {japan_angle} A focused Makuake or GreenFunding "
                "launch could showcase exactly that."
            ),
            "exclusive": (
                "With exclusivity, we can commit the marketing budget and operational "
                "resources to maximize the launch, handle Japanese customer support, "
                "and manage import and certification (e.g., PSE) where required."
            ),
            "cta": (
                "Could we set up a short online meeting to discuss terms, timing, and "
                "expected volumes?"
            ),
        }
    # followup
    return {
        "open": (
            "I wanted to gently follow up on my previous message about bringing "
            f"{title} to the Japanese market. I understand you're busy, so even a "
            "brief reply would be appreciated."
        ),
        "intro": intro,
        "maker": maker_appeal,
        "value": (
            f"{compliment} {japan_angle} That's exactly why I'm keen to help "
            "introduce it on Makuake or GreenFunding."
        ),
        "exclusive": (
            "I'm also happy to share how exclusive distribution for Japan could work."
        ),
        "cta": (
            "A 20-minute online meeting at your convenience would be more than enough. "
            "Looking forward to hearing from you."
        ),
    }


def _render_body(parts: dict[str, str], maker: str, tone: EmailTone) -> str:
    """トーンに応じて本文パーツを組み立てる。"""
    greeting = _greeting(maker, tone)

    if tone is EmailTone.short:
        order = ["open", "value", "cta"]
    elif tone is EmailTone.executive:
        order = ["open", "value", "exclusive", "cta"]
    elif tone is EmailTone.detailed:
        order = ["open", "intro", "maker", "value", "exclusive", "cta"]
    elif tone is EmailTone.friendly:
        order = ["open", "intro", "maker", "value", "exclusive", "cta"]
    else:  # professional
        order = ["open", "intro", "maker", "value", "exclusive", "cta"]

    paragraphs = [parts[k] for k in order if parts.get(k)]
    return greeting + "\n\n" + "\n\n".join(paragraphs)


def _japanese_summary(
    project: Project, email_type: EmailType, tone: EmailTone
) -> str:
    """送信前の確認用：日本語要約（狙い／主な価値／独占販売権／次のアクション）。"""
    title = project.title
    aim = {
        EmailType.initial_outreach: "海外メーカーへの初回接触・関係構築",
        EmailType.exclusive_rights: "日本での独占販売パートナーシップの提案",
        EmailType.followup: "前回メールへの丁寧なフォローアップ",
    }[email_type]
    value = (
        f"「{title}」の魅力とクラファン実績に触れ、Makuake / GreenFunding での"
        "日本展開の可能性を伝えています。"
    )
    exclusive = "あり（日本での独占販売権について相談したい旨を記載）"
    next_action = "オンラインミーティングの打診"
    return (
        f"このメールの狙い：{aim}\n"
        f"主な価値：{value}\n"
        f"独占販売権への言及：{exclusive}\n"
        f"次のアクション：{next_action}\n"
        f"トーン：{tone.value}"
    )


class MockEmailGenerator(EmailGenerator):
    name = "mock-email-v1"

    def generate(
        self,
        project: Project,
        ctx: SenderContext | None = None,
        tone: EmailTone = DEFAULT_TONE,
    ) -> list[EmailDraftResult]:
        ctx = ctx or SenderContext.fallback()
        maker = _maker(project)
        title = project.title
        # 商品・メーカーごとの個別化材料を先に作る
        p = build_personalization(project)

        drafts: list[EmailDraftResult] = []
        for email_type in (
            EmailType.initial_outreach,
            EmailType.exclusive_rights,
            EmailType.followup,
        ):
            options = _subject_options(email_type, title)
            parts = _parts(project, email_type, ctx, p)
            body = append_signature(_render_body(parts, maker, tone), ctx)
            drafts.append(
                EmailDraftResult(
                    email_type=email_type,
                    subject=options[0],
                    subject_options=options,
                    selected_subject=options[0],
                    body=body,
                    tone=tone.value,
                    japanese_summary=_japanese_summary(project, email_type, tone),
                    personalization_context=p,
                    personalized_compliment=p["personalized_compliment"],
                    product_highlights=p["product_highlights"],
                    model=self.name,
                )
            )
        return drafts
