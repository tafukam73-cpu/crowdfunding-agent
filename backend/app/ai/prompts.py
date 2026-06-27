"""営業メール生成の共通プロンプト・署名ロジック。

モック生成器・Claude 生成器の双方がここを利用する。

- SenderContext: 差出人/会社情報（DB のメール設定 or .env フォールバック）
- システムプロンプト / 種別ごとの狙い / 本文プロンプト組み立て
- 署名テンプレートのレンダリング（AI には生成させず固定連結する）
- 会社紹介文のトリム

設計方針：本文（body）に署名は AI に書かせず、生成後にここで固定テンプレートを
連結する。これによりモック・Claude のどちらでも署名が必ず末尾に入る。
"""
from __future__ import annotations

import enum
import re

from pydantic import BaseModel

from app.config import settings
from app.models.email_draft import EmailType

# AI へ渡す会社紹介文の最大文字数（長すぎる場合はトリム）
COMPANY_PROFILE_MAX_CHARS = 800


class EmailTone(str, enum.Enum):
    """営業メールのトーン（生成時に選択）。"""

    professional = "professional"  # 標準的で丁寧
    friendly = "friendly"          # 親しみやすい
    executive = "executive"        # 経営者向けに簡潔
    short = "short"                # 短文
    detailed = "detailed"          # 詳しめ


# トーンごとの英文指示（プロンプトに含める）
TONE_INSTRUCTIONS: dict[EmailTone, str] = {
    EmailTone.professional: (
        "Tone: standard, polite and professional. Warm but businesslike. "
        "Aim for roughly 120-180 words."
    ),
    EmailTone.friendly: (
        "Tone: warm, friendly and approachable, while staying professional. "
        "Use a personable, conversational voice. Aim for roughly 120-180 words."
    ),
    EmailTone.executive: (
        "Tone: concise and high-level, written for a busy executive. Lead with the "
        "value, get to the point quickly, minimal small talk. Aim for roughly "
        "90-130 words."
    ),
    EmailTone.short: (
        "Tone: very short and to the point. A few crisp sentences only, no padding. "
        "Aim for roughly 60-90 words while still covering the key points."
    ),
    EmailTone.detailed: (
        "Tone: thorough and detailed. Elaborate a little more on the product's value "
        "and how the Japan launch and exclusive distribution would work. Aim for "
        "roughly 180-240 words."
    ),
}

TONE_LABELS: dict[EmailTone, str] = {
    EmailTone.professional: "Professional（標準・丁寧）",
    EmailTone.friendly: "Friendly（親しみやすい）",
    EmailTone.executive: "Executive（経営者向け・簡潔）",
    EmailTone.short: "Short（短文）",
    EmailTone.detailed: "Detailed（詳しめ）",
}

DEFAULT_TONE = EmailTone.professional

# カテゴリ別の強調ポイント（部分一致でマッチ。プロンプトに含める）。
# 例：ガジェット → innovation / usability / tech-savvy Japanese consumers
CATEGORY_EMPHASIS: dict[str, str] = {
    "ガジェット": "innovation, usability, and tech-savvy Japanese consumers",
    "テック": "innovation, usability, and tech-savvy Japanese consumers",
    "オーディオ": "sound quality, design, and tech-savvy Japanese consumers",
    "アウトドア": "the outdoor lifestyle, compact design, and the Japanese camping market",
    "キャンプ": "the outdoor lifestyle, compact design, and the Japanese camping market",
    "ペット": "pet owners, safety, and everyday convenience",
    "美容": "wellness, design, and fitting into a daily routine",
    "健康": "wellness, design, and fitting into a daily routine",
    "ヘルス": "wellness, design, and fitting into a daily routine",
}

DEFAULT_CATEGORY_EMPHASIS = "innovation, visual appeal, and crowdfunding potential"


def emphasis_for(category: str | None) -> str:
    """カテゴリ文字列から強調ポイントを返す（部分一致、未該当は既定）。"""
    if category:
        for key, phrase in CATEGORY_EMPHASIS.items():
            if key in category:
                return phrase
    return DEFAULT_CATEGORY_EMPHASIS


def build_greeting(
    maker_name: str | None = None,
    person_name: str | None = None,
    department: str | None = None,
) -> str:
    """冒頭挨拶を取得できた情報から組み立てる（モック・Claude 共通）。

    優先順位（要件）:
      1. 担当者名あり → "Dear {Person Name},"
      2. 担当部署あり → "Dear {Department Name},"
      3. メーカー名のみ → "Hello {Maker Name} Team,"
      4. 何も無い → "Hello Team,"
    """
    if person_name and person_name.strip():
        return f"Dear {person_name.strip()},"
    if department and department.strip():
        return f"Dear {department.strip()},"
    if maker_name and maker_name.strip():
        return f"Hello {maker_name.strip()} Team,"
    return "Hello Team,"


def render_research_block(research: dict) -> str:
    """企業リサーチ結果をプロンプト用テキストに整形する。

    本文をより具体化するための材料。固定文の丸写しは避け、自然に織り込ませる。
    """
    def _join(key: str) -> str:
        v = research.get(key)
        return "; ".join(str(x) for x in v if x) if isinstance(v, list) else ""

    lines = [
        "# Company research (verified material — prefer these specifics; do NOT copy "
        "verbatim, weave naturally)",
        f"Brand summary: {research.get('brand_summary', '') or ''}",
        f"Company mission: {research.get('company_mission', '') or ''}",
        f"Product summary: {research.get('product_summary', '') or ''}",
        f"Key product features: {_join('key_product_features')}",
        f"Brand strengths: {_join('brand_strengths')}",
        f"Differentiation: {_join('differentiation_points')}",
        f"Japan market fit: {research.get('japan_market_fit', '') or ''}",
        f"Suggested compliment: {research.get('personalized_compliment', '') or ''}",
        f"Outreach angles to make: {_join('outreach_angles')}",
        f"Cautions (avoid these): {_join('risks_or_cautions')}",
    ]
    return "\n".join(lines)

# 署名の既定テンプレート（メール設定未登録／未設定時に使用）
DEFAULT_SIGNATURE_TEMPLATE = (
    "Best regards,\n\n"
    "{sender_name}\n"
    "{sender_title}\n"
    "{sender_department}\n"
    "{company_name}\n\n"
    "Email: {sender_email}\n"
    "Phone: {phone}\n"
    "Website: {website_url}"
)


class SenderContext(BaseModel):
    """メール生成に使う差出人・会社情報。

    DB のメール設定（email_settings）から作るのが基本。未登録時は .env の
    SENDER_NAME / SENDER_COMPANY をフォールバックに使い、生成が必ず動くようにする。
    """

    company_name: str = ""
    sender_name: str = ""
    sender_title: str = ""
    sender_department: str = ""
    sender_email: str = ""
    phone: str = ""
    website_url: str = ""
    company_profile: str = ""
    signature_template: str = ""

    @classmethod
    def fallback(cls) -> "SenderContext":
        """メール設定未登録時のフォールバック（.env の差出人情報）。"""
        return cls(
            company_name=settings.sender_company,
            sender_name=settings.sender_name,
        )

    @classmethod
    def from_settings(cls, row: object | None) -> "SenderContext":
        """email_settings の ORM 行から SenderContext を作る。

        row が None（未登録）ならフォールバックを返す。各項目が空なら差出人名・
        会社名は .env の値で補完する。
        """
        if row is None:
            return cls.fallback()
        return cls(
            company_name=(getattr(row, "company_name", None) or settings.sender_company),
            sender_name=(getattr(row, "sender_name", None) or settings.sender_name),
            sender_title=getattr(row, "sender_title", None) or "",
            sender_department=getattr(row, "sender_department", None) or "",
            sender_email=getattr(row, "sender_email", None) or "",
            phone=getattr(row, "phone", None) or "",
            website_url=getattr(row, "website_url", None) or "",
            company_profile=getattr(row, "company_profile", None) or "",
            signature_template=getattr(row, "signature_template", None) or "",
        )


SYSTEM_PROMPT = (
    "You are a business development manager at a Japanese distribution company "
    "that helps overseas makers launch their products in Japan via leading "
    "crowdfunding platforms (Makuake and GreenFunding) and secure exclusive "
    "Japanese distribution.\n"
    "Write warm, natural sales emails in fluent English. Be specific and "
    "genuine, never pushy or overly salesy. Never invent facts about the product "
    "or the maker; only use what you are told.\n"
    "Do NOT add a signature, closing block, or contact details — the signature is "
    "appended separately by the system. End the body with a natural closing "
    "sentence only.\n"
    "Output must follow the given JSON schema exactly. Provide exactly three "
    "distinct subject line options (subject_options), the email body (body), and a "
    "short Japanese summary (japanese_summary) so the sender can review the email "
    "quickly before sending."
)

# 種別ごとの狙い（プロンプトに含める）
TYPE_INTENT: dict[EmailType, str] = {
    EmailType.initial_outreach: (
        "First-contact outreach: introduce yourself and your company briefly, "
        "show genuine, specific admiration for the product and its crowdfunding "
        "success, explain concretely why it has potential in the Japanese market, "
        "mention you would love to launch it on Makuake / GreenFunding, raise the "
        "idea of discussing exclusive Japanese distribution rights, and propose a "
        "short online meeting."
    ),
    EmailType.exclusive_rights: (
        "Propose an exclusive Japan distribution partnership. Reaffirm respect for "
        "their crowdfunding results, explain why exclusivity lets you commit the "
        "marketing/operational resources to maximize a Makuake / GreenFunding "
        "launch, and ask to discuss terms over a short online meeting."
    ),
    EmailType.followup: (
        "Polite, brief follow-up to a previous unanswered message; lower the bar "
        "to a quick reply and gently restate the Japan launch / exclusive "
        "distribution opportunity. Offer a short online meeting at their "
        "convenience."
    ),
}

# プロンプトに常に含める「自然な営業メールに含めるべき要素」
EMAIL_GUIDELINES = (
    "Naturally weave the following into the body (do not use a bullet list, write "
    "flowing prose). Make it specific to THIS product and maker — avoid generic, "
    "template-like phrasing:\n"
    "- Refer to the product by name (use the exact product name).\n"
    "- Explain specifically why you were impressed by the product.\n"
    "- Include one specific, genuine complimentary sentence about the product "
    "(not a generic compliment).\n"
    "- Mention 1-2 concrete appeals/features of the product.\n"
    "- Give a concrete reason you want to work with this particular maker/team.\n"
    "- Show respect for its crowdfunding track record (backers / funding).\n"
    "- Give a concrete reason it has potential in the Japanese market.\n"
    "- Express that you would like to launch it on Makuake / GreenFunding.\n"
    "- Raise that you would like to discuss exclusive Japanese distribution rights.\n"
    "- Propose a short online meeting.\n"
    "- Keep the tone natural and human, not aggressively salesy."
)

# 日本語要約に含める要素（プロンプトに含める）
SUMMARY_GUIDELINES = (
    "Also write japanese_summary in Japanese (3-5 short lines, plain text) so the "
    "sender can review before sending. It must cover: このメールの狙い / 相手に"
    "伝えている主な価値 / 独占販売権への言及の有無 / 次のアクション。"
)


def trim_company_profile(profile: str | None, max_chars: int = COMPANY_PROFILE_MAX_CHARS) -> str:
    """会社紹介文を AI プロンプト用にトリムする（長すぎる場合は末尾を省略）。"""
    if not profile:
        return ""
    text = profile.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + " …"


def build_email_prompt(
    project: object,
    email_type: EmailType,
    ctx: SenderContext,
    type_label: str,
    tone: EmailTone = DEFAULT_TONE,
    personalization: dict | None = None,
    research: dict | None = None,
) -> str:
    """本文生成用のユーザープロンプトを組み立てる。

    personalization は personalization.build_personalization() の出力（任意）。
    research は企業リサーチ結果（任意）。いずれも渡された場合はプロンプトに含め、
    商品・企業ごとに本文を具体化する。
    """
    profile = trim_company_profile(ctx.company_profile)
    sender_line = ", ".join(
        part
        for part in (ctx.sender_name, ctx.sender_title, ctx.company_name)
        if part
    ) or settings.sender_company
    category = getattr(project, "category", None)
    emphasis = emphasis_for(category)
    # 冒頭挨拶は規則に従って決定し、本文の先頭で必ずこの行を使わせる
    greeting = build_greeting(maker_name=getattr(project, "maker_name", None))

    lines = [
        f"Write a sales email of type '{type_label}'.",
        f"Goal: {TYPE_INTENT[email_type]}",
        "",
        TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS[DEFAULT_TONE]),
        "",
        EMAIL_GUIDELINES,
        "",
        SUMMARY_GUIDELINES,
        "",
        f"For this product category, lean the wording toward: {emphasis}.",
        "",
        f'Begin the body with exactly this greeting line, unchanged: "{greeting}"',
        "",
        f"You are writing on behalf of: {sender_line}.",
    ]
    if profile:
        lines += [
            "",
            "# About our company (context, do not quote verbatim)",
            profile,
        ]
    if personalization:
        # 遅延 import で循環参照を避ける
        from app.ai.personalization import render_personalization_block

        lines += ["", render_personalization_block(personalization)]
    if research:
        lines += ["", render_research_block(research)]
    lines += [
        "",
        "# Product",
        f"Title: {getattr(project, 'title', '')}",
        f"Maker: {getattr(project, 'maker_name', None) or 'the maker'}",
        f"Category: {category or ''}",
        f"Source platform: {getattr(project, 'source_site', None) or ''}",
        f"Funding raised: {getattr(project, 'raised_amount', None) or ''} "
        f"{getattr(project, 'currency', None) or ''}",
        f"Backers: {getattr(project, 'backers_count', None) or ''}",
        f"Description: {getattr(project, 'description', None) or ''}",
        "",
        "Return JSON with keys: subject_options (exactly 3 distinct strings; at least "
        "one MUST include the product name), body, japanese_summary. Do not include "
        "any signature in body.",
    ]
    return "\n".join(lines)


def _clean_signature(text: str) -> str:
    """署名レンダリング後の空ラベル行・余分な空行を整理する。"""
    cleaned_lines: list[str] = []
    for line in text.split("\n"):
        # 値が空のラベル行（例 "Email:" / "Phone:"）は落とす
        if re.fullmatch(r"[A-Za-z][\w ]*:\s*", line):
            continue
        cleaned_lines.append(line.rstrip())
    out = "\n".join(cleaned_lines)
    # 3 連以上の改行を 2 連へ圧縮
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def render_signature(ctx: SenderContext) -> str:
    """保存済みテンプレート（or 既定）から署名文字列を生成する。

    AI には生成させず固定連結する。空フィールドはプレースホルダを空文字に置換し、
    空ラベル行は除去する。
    """
    template = ctx.signature_template.strip() if ctx.signature_template else ""
    if not template:
        template = DEFAULT_SIGNATURE_TEMPLATE

    fields = {
        "company_name": ctx.company_name,
        "sender_name": ctx.sender_name,
        "sender_title": ctx.sender_title,
        "sender_department": ctx.sender_department,
        "sender_email": ctx.sender_email,
        "phone": ctx.phone,
        "website_url": ctx.website_url,
    }

    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:  # 未知のプレースホルダは空に
            return ""

    rendered = template.format_map(_SafeDict(fields))
    return _clean_signature(rendered)


def append_signature(body: str, ctx: SenderContext) -> str:
    """本文末尾へ署名を連結する（モック・Claude 共通）。"""
    signature = render_signature(ctx)
    if not signature:
        return body
    return f"{body.rstrip()}\n\n{signature}\n"
