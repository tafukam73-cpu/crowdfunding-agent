"""問い合わせフォーム / SNS DM 用の短文アウトリーチ文を生成する。

メールアドレスが見つからない案件（recommended_channel が contact_form /
instagram / linkedin / facebook）向けに、フォームや DM にそのまま貼り付けられる
短い営業文をロジックで組み立てる。既存の営業メール（EmailDraft）とは別物で、
署名や独占販売権の詳細などは入れず、要点だけを 300〜600 文字程度にまとめる。

必ず含める要素（要件）:
  - 商品名
  - 感銘を受けた理由
  - 日本市場で紹介したい旨
  - 担当者確認（誰に連絡すべきか）

純粋関数として実装し、ネットワーク・DB 非依存でテストできるようにする。
"""
from __future__ import annotations

import re

from app.ai.personalization import build_personalization
from app.ai.prompts import SenderContext
from app.models.project import Project

# 短文アウトリーチ文を出す対象チャネル（メール以外）
OUTREACH_CHANNELS: tuple[str, ...] = (
    "contact_form",
    "instagram",
    "linkedin",
    "facebook",
)

CHANNEL_LABELS: dict[str, str] = {
    "contact_form": "問い合わせフォーム",
    "instagram": "Instagram DM",
    "linkedin": "LinkedIn メッセージ",
    "facebook": "Facebook メッセージ",
}

# チャネルごとの書き出し（フォーム/LinkedIn は丁寧、Instagram/Facebook は親しみやすく）
_OPENERS: dict[str, str] = {
    "contact_form": "Hello,",
    "instagram": "Hi there!",
    "linkedin": "Hello,",
    "facebook": "Hi there!",
}

# チャネルごとの「どこで見つけたか」の一文
_DISCOVERED: dict[str, str] = {
    "contact_form": "I'm reaching out through your website",
    "instagram": "I came across your Instagram",
    "linkedin": "I came across your company on LinkedIn",
    "facebook": "I came across your Facebook page",
}

MIN_CHARS = 300
MAX_CHARS = 600


def _cap_sentence(text: str, max_chars: int = 170) -> str:
    """長すぎる材料文を文末で軽く丸める（短文向けに冗長さを避ける）。"""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # 直近の句点で切る。無ければそのまま省略記号。
    m = re.search(r"^(.*[.!?])\s", cut)
    if m:
        return m.group(1).strip()
    return cut.rstrip() + "…"


def _compliment(p: dict, research: dict | None) -> str:
    """感銘を受けた理由（リサーチがあれば具体的なものを優先）。"""
    if research and research.get("personalized_compliment"):
        return _cap_sentence(research["personalized_compliment"])
    return _cap_sentence(p["personalized_compliment"])


def _japan_angle(p: dict, research: dict | None) -> str:
    """日本市場での適合性（リサーチがあれば優先）。"""
    if research and research.get("japan_market_fit"):
        return _cap_sentence(research["japan_market_fit"])
    return _cap_sentence(p["japan_market_angle"])


def _fit_length(sentences: list[str], droppable: list[str]) -> str:
    """文を順序どおり連結しつつ 300〜600 文字程度に収める。

    上限超過の場合のみ、droppable に挙げた文を（リスト順＝優先度順に）落として
    文字数を抑える。順序は維持する。必須文だけで上限を超える場合は要素欠落を
    避けてそのまま返す。下限は材料の都合で届かないこともあるため努力目標とする。
    """
    skip: set[str] = set()

    def render() -> str:
        return " ".join(s for s in sentences if s and s not in skip).strip()

    text = render()
    for s in droppable:
        if len(text) <= MAX_CHARS:
            break
        skip.add(s)
        text = render()
    return text


def build_outreach_message(
    project: Project,
    channel: str,
    ctx: SenderContext | None = None,
    research: dict | None = None,
) -> dict:
    """短文アウトリーチ文を組み立てて返す。

    Returns: {channel, channel_label, text, char_count}
    """
    if channel not in OUTREACH_CHANNELS:
        channel = "contact_form"
    ctx = ctx or SenderContext.fallback()

    p = build_personalization(project)
    product = project.title
    sender = (ctx.sender_name or "").strip() or "the team"
    company = (ctx.company_name or "").strip() or "a Japan-based distribution team"
    compliment = _compliment(p, research)
    japan = _japan_angle(p, research)

    intro = (
        f"{_OPENERS[channel]} I'm {sender} from {company}. We help overseas products "
        "launch in Japan on Makuake and GreenFunding."
    )
    discovered = f"{_DISCOVERED[channel]} and was immediately drawn to {product}."
    # value は商品名を必ず含める（感銘文がリサーチ由来だと商品名を含まない場合がある）
    value = (
        f"We'd love to introduce {product} to the Japanese market and explore "
        "distribution together."
    )
    ask = (
        "Could you point me to the right person for partnership or distribution "
        "inquiries?"
    )
    thanks = "Thank you for your time!"

    # 表示順（商品名・感銘を受けた理由・日本市場・日本展開の意向・担当者確認）。
    sentences = [intro, discovered, compliment, japan, value, ask, thanks]
    # 上限超過時に落としてよい文（優先度順：まず日本市場アングル、次に発見の一文）。
    droppable = [japan, discovered]

    text = _fit_length(sentences, droppable)
    return {
        "channel": channel,
        "channel_label": CHANNEL_LABELS.get(channel, channel),
        "text": text,
        "char_count": len(text),
    }
