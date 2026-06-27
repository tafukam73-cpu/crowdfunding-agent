"""モック返信メール AI サポート。

外部 API を使わず、受信メール本文をヒューリスティックに解析し、解析結果と英語の
返信案を組み立てる。Claude 未設定時でも画面・DB・Gmail 連携を確認できる。

署名は付けない（サービス層で email_settings の署名を末尾連結する）。
"""
from __future__ import annotations

import re

from app.ai.prompts import build_greeting
from app.ai.reply_assistant import (
    DEFAULT_REPLY_TONE,
    IncomingEmail,
    ReplyAssistant,
    ReplyAssistResult,
    ReplyTone,
)
from app.models.project import Project

_JP_CHARS = re.compile(r"[぀-ヿ一-鿿]")

# intent 判定キーワード（上から優先）
_INTENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("already_has_distributor", ("already have a distributor", "existing distributor",
                                  "already work with", "current partner", "existing partner")),
    ("not_interested", ("not interested", "no thank", "we'll pass", "not a fit",
                         "decline", "not at this time")),
    ("requests_call", ("call", "meeting", "schedule", "zoom", "google meet", "teams",
                        "let's talk", "hop on")),
    ("asks_terms", ("price", "pricing", "cost", "terms", "margin", "moq",
                    "minimum order", "wholesale price", "quote", "commission")),
    ("needs_more_info", ("more info", "more information", "details", "spec",
                         "specification", "sample", "catalog", "deck", "data sheet")),
    ("interested", ("interested", "sounds great", "we'd love", "love to",
                    "happy to", "excited", "keen")),
]

_POSITIVE = ("interested", "great", "love", "excited", "thank", "happy", "glad",
             "looking forward", "keen", "appreciate")
_NEGATIVE = ("not interested", "no thank", "unfortunately", "decline", "concern",
             "expensive", "too high", "problem", "cannot", "won't")


def _detect_language(text: str) -> str:
    return "ja" if _JP_CHARS.search(text or "") else "en"


def _detect_intent(text: str) -> str:
    low = (text or "").lower()
    for intent, kws in _INTENT_RULES:
        if any(k in low for k in kws):
            return intent
    return "unclear"


def _detect_sentiment(text: str) -> str:
    low = (text or "").lower()
    neg = sum(1 for k in _NEGATIVE if k in low)
    pos = sum(1 for k in _POSITIVE if k in low)
    if neg > pos:
        return "negative"
    if pos > 0:
        return "positive"
    return "neutral"


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。！？])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


# intent → (日本語ラベル, 返信の本旨, 求めていること, 推奨次アクション)
_INTENT_JA = {
    "interested": "前向きな関心を示している",
    "needs_more_info": "追加情報（仕様・資料・サンプル）を求めている",
    "asks_terms": "価格・取引条件について尋ねている",
    "requests_call": "通話／オンラインミーティングを希望している",
    "not_interested": "今回は見送りの意向",
    "already_has_distributor": "既存の販売代理店がいると示唆している",
    "unclear": "意図が明確でない",
}


def _reply_core(intent: str, title: str) -> tuple[str, str, list[str], str]:
    """intent ごとの返信の中核文・要求・推奨次アクションを返す。"""
    if intent == "needs_more_info":
        answer = (
            "Happy to share more details — I can send a product overview, "
            f"specifications, and reference materials for {title}, and arrange "
            "samples if useful."
        )
        requested = ["追加情報・仕様・資料の提供", "必要ならサンプル送付"]
        nxt = "資料一式を送付し、確認後にオンラインミーティングを打診する"
    elif intent == "asks_terms":
        answer = (
            "I'd be glad to walk through pricing and partnership terms. The exact "
            "numbers depend on volumes and exclusivity, so a short call would let me "
            "tailor a proposal."
        )
        requested = ["価格・取引条件の提示", "数量・独占条件に応じた見積り"]
        nxt = "条件のたたき台を用意し、オンラインミーティングで擦り合わせる"
    elif intent == "requests_call":
        answer = (
            "A call sounds great. I'm flexible across time zones — please share a few "
            "slots that suit you and I'll make it work."
        )
        requested = ["オンラインミーティングの日程調整"]
        nxt = "候補日時を3つ提示してミーティングを設定する"
    elif intent == "interested":
        answer = (
            "Thrilled to hear your interest. I'd love to map out a concrete plan for a "
            f"Japanese launch of {title} and the next steps together."
        )
        requested = ["具体的な次ステップの提示"]
        nxt = "進め方の概要を共有し、オンラインミーティングを設定する"
    elif intent == "not_interested":
        answer = (
            "Thank you for letting me know, and no problem at all. If timing changes "
            "down the road, I'd be glad to revisit a Japan launch together."
        )
        requested = ["（今回は対応不要。関係維持）"]
        nxt = "丁寧にお礼を伝え、将来の再接触余地を残す"
    elif intent == "already_has_distributor":
        answer = (
            "Thanks for sharing that. If your current setup leaves any room for Japan "
            "specifically — for example crowdfunding-led launches on Makuake or "
            "GreenFunding — I'd welcome a short conversation."
        )
        requested = ["日本市場（クラファン）での余地確認"]
        nxt = "既存代理店との棲み分け可能性を確認するミーティングを打診する"
    else:  # unclear / interested fallback
        answer = (
            "Thank you for your reply. To make sure I address your needs precisely, "
            "could you let me know your main questions? Meanwhile, I'm confident "
            f"{title} could resonate with Japanese backers."
        )
        requested = ["論点の明確化"]
        nxt = "確認事項を質問しつつ、価値提案を簡潔に再提示する"
    return answer, "", requested, nxt


class MockReplyAssistant(ReplyAssistant):
    name = "mock-reply-v1"

    def assist(
        self,
        project: Project,
        incoming: IncomingEmail,
        tone: ReplyTone = DEFAULT_REPLY_TONE,
    ) -> ReplyAssistResult:
        body = incoming.body or ""
        title = project.title
        intent = _detect_intent(body)
        sentiment = _detect_sentiment(body)
        language = _detect_language(body)
        answer, _, requested, nxt = _reply_core(intent, title)

        key_points = _sentences(body)[:3] or ["（本文から要点を抽出できませんでした）"]

        japanese_summary = (
            f"差出人は{_INTENT_JA.get(intent, '意図不明')}。"
            f"温度感は{ {'positive':'前向き','neutral':'中立','negative':'慎重'}[sentiment] }。"
            f"「{title}」に関する返信で、上記の要点が述べられています。"
        )

        risks = []
        if intent == "already_has_distributor":
            risks.append("既存代理店がいるため、独占提案は棲み分けを前提に慎重に進める")
        if intent == "not_interested":
            risks.append("押し売りにならないよう、関係維持を優先する")
        if sentiment == "negative":
            risks.append("相手は慎重姿勢のため、価値と誠実さを丁寧に伝える")
        risks.append("事実誤認を避け、確約できない条件は断定しない")

        # --- 返信案（署名なし） ---
        greeting = build_greeting(maker_name=project.maker_name)
        thanks = "Thank you very much for getting back to me — I really appreciate it."
        japan = (
            f"We remain very keen to introduce {title} to the Japanese market through a "
            "Makuake or GreenFunding launch."
        )
        exclusive = (
            "Where it fits, we'd also be glad to discuss exclusive Japanese "
            "distribution so we can fully commit to the launch."
        )
        cta = (
            "Would a short online meeting in the coming days work for you? I'm happy to "
            "fit your schedule."
        )

        if tone is ReplyTone.concise:
            paras = [thanks, answer, cta]
        elif tone is ReplyTone.executive:
            paras = [thanks, answer, japan, cta]
        elif tone is ReplyTone.detailed:
            paras = [thanks, answer, japan, exclusive, cta]
        elif tone is ReplyTone.friendly:
            paras = [thanks, answer, japan, exclusive, cta]
        else:  # professional
            paras = [thanks, answer, japan, exclusive, cta]
        # not_interested では独占の押し込みを避ける
        if intent == "not_interested":
            paras = [thanks, answer]

        reply_body = greeting + "\n\n" + "\n\n".join(paras)
        subject = incoming.subject or title
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"

        return ReplyAssistResult(
            detected_language=language,
            japanese_summary=japanese_summary,
            intent=intent,
            sentiment=sentiment,
            key_points=key_points,
            requested_actions=requested,
            risks_or_cautions=risks,
            recommended_next_action=nxt,
            reply_subject=reply_subject,
            reply_body=reply_body,
            model=self.name,
        )
