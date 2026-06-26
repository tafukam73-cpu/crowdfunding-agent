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

import re

from pydantic import BaseModel

from app.config import settings
from app.models.email_draft import EmailType

# AI へ渡す会社紹介文の最大文字数（長すぎる場合はトリム）
COMPANY_PROFILE_MAX_CHARS = 800

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
    "Write warm, professional sales emails in natural English. Be specific and "
    "genuine, never pushy or overly salesy. Never invent facts about the product "
    "or the maker; only use what you are told.\n"
    "Do NOT add a signature, closing block, or contact details — the signature is "
    "appended separately by the system. End the body with a natural closing "
    "sentence only.\n"
    "Output must follow the given JSON schema exactly (keys: subject, body)."
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
    "Naturally weave in the following (do not use a bullet list, write flowing "
    "prose):\n"
    "- Refer to the product by name.\n"
    "- Mention 1-2 specific, concrete appeals of the product.\n"
    "- Show respect for its crowdfunding track record (backers / funding).\n"
    "- Give a concrete reason it has potential in the Japanese market.\n"
    "- Express that you would like to launch it on Makuake / GreenFunding.\n"
    "- Raise that you would like to discuss exclusive Japanese distribution rights.\n"
    "- Propose a short online meeting.\n"
    "- Keep the tone natural and human, not aggressively salesy.\n"
    "Keep the email concise (roughly 120-180 words)."
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
    project: object, email_type: EmailType, ctx: SenderContext, type_label: str
) -> str:
    """本文生成用のユーザープロンプトを組み立てる。"""
    profile = trim_company_profile(ctx.company_profile)
    sender_line = ", ".join(
        part
        for part in (ctx.sender_name, ctx.sender_title, ctx.company_name)
        if part
    ) or settings.sender_company

    lines = [
        f"Write a sales email of type '{type_label}'.",
        f"Goal: {TYPE_INTENT[email_type]}",
        "",
        EMAIL_GUIDELINES,
        "",
        f"You are writing on behalf of: {sender_line}.",
    ]
    if profile:
        lines += [
            "",
            "# About our company (context, do not quote verbatim)",
            profile,
        ]
    lines += [
        "",
        "# Product",
        f"Title: {getattr(project, 'title', '')}",
        f"Maker: {getattr(project, 'maker_name', None) or 'the maker'}",
        f"Category: {getattr(project, 'category', None) or ''}",
        f"Source platform: {getattr(project, 'source_site', None) or ''}",
        f"Description: {getattr(project, 'description', None) or ''}",
        "",
        "Return JSON with keys: subject, body. Do not include any signature in body.",
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
