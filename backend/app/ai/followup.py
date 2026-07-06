"""フォローアップ営業メールの文面生成（決定的・API キー不要）。

初回営業メールの後、返信が無い案件へ送る 2 通目・3 通目のフォローアップを、
最終営業日からの経過日数に応じて 3 段階で作り分ける：

  - light（3〜6日）   : 軽い確認（そっと再送）
  - repropose（7〜13日）: 再提案（価値を要約し、短い打ち合わせを提案）
  - final（14日以上）  : 最終フォロー（低圧力で締め、いつでも再開できる余地を残す）

初回メール同様に SenderContext（差出人情報）・企業リサーチ・担当者情報を活用する。
本モジュールは決定的でネットワーク不要（テスト可能）。AI 生成は将来差し替え可能。
"""
from __future__ import annotations

from urllib.parse import quote

from app.ai.personalization import build_personalization
from app.ai.prompts import SenderContext
from app.models.project import Project

# 経過日数のしきい値（workflow_service のフォロー判定と一致させる）。
FOLLOWUP_MIN_DAYS = 3
FOLLOWUP_REPROPOSE_DAYS = 7
FOLLOWUP_FINAL_DAYS = 14

FOLLOWUP_STAGES = ("light", "repropose", "final")
FOLLOWUP_STAGE_LABELS: dict[str, str] = {
    "light": "軽い確認",
    "repropose": "再提案",
    "final": "最終フォロー",
}
# stage -> workflow_service の follow_up_level 表記
STAGE_TO_LEVEL = {"light": "normal", "repropose": "high", "final": "final"}


def stage_for_days(days: int | None) -> str | None:
    """最終営業からの経過日数をフォロー段階に変換する。3日未満は None。"""
    if days is None or days < FOLLOWUP_MIN_DAYS:
        return None
    if days >= FOLLOWUP_FINAL_DAYS:
        return "final"
    if days >= FOLLOWUP_REPROPOSE_DAYS:
        return "repropose"
    return "light"


def _greeting(contact: dict | None, maker_name: str | None) -> str:
    if contact:
        name = (contact.get("name") or "").strip()
        dept = (contact.get("department") or "").strip()
        if name:
            return f"Dear {name},"
        if dept:
            return f"Dear {dept} Team,"
    if maker_name and maker_name.strip():
        return f"Hello {maker_name.strip()} Team,"
    return "Hello there,"


def _signature(ctx: SenderContext) -> str:
    sender = (ctx.sender_name or "").strip() or "the team"
    lines = ["Best regards,", sender]
    title = (ctx.sender_title or "").strip()
    company = (ctx.company_name or "").strip()
    if title and company:
        lines.append(f"{title}, {company}")
    elif company:
        lines.append(company)
    contact_bits = [b for b in ((ctx.sender_email or "").strip(),
                                (ctx.website_url or "").strip()) if b]
    if contact_bits:
        lines.append(" | ".join(contact_bits))
    return "\n".join(lines)


def _japan_angle(p: dict, research: dict | None) -> str:
    if research and research.get("japan_market_fit"):
        return str(research["japan_market_fit"]).strip()
    return str(p.get("japan_market_angle") or "").strip()


def build_followup_message(
    project: Project,
    *,
    stage: str,
    days: int | None = None,
    ctx: SenderContext | None = None,
    research: dict | None = None,
    contact: dict | None = None,
) -> dict:
    """フォローアップ本文を段階別に組み立てて返す。

    Returns: {stage, stage_label, subject, subject_options, body, japanese_summary}
    """
    if stage not in FOLLOWUP_STAGES:
        stage = "light"
    ctx = ctx or SenderContext.fallback()
    p = build_personalization(project)
    product = project.title
    greeting = _greeting(contact, project.maker_name)
    sign = _signature(ctx)
    japan = _japan_angle(p, research)

    if stage == "light":
        subject_options = [
            f"Quick follow-up: {product} × Japan",
            f"Following up on {product}",
            f"Re: {product} — a quick check-in",
        ]
        para = (
            f"I wanted to gently follow up on my previous email about bringing "
            f"{product} to the Japanese market via Makuake / GreenFunding.\n\n"
            "I know things get busy — is a Japan launch something your team would be "
            "open to exploring? I'm happy to share how we typically bring overseas "
            "products to Japanese backers."
        )
        jp_summary = f"{FOLLOWUP_STAGE_LABELS[stage]}：前回メールへの軽い再確認。日本展開の意向を尋ねる。"
    elif stage == "repropose":
        subject_options = [
            f"Re-sharing our Japan proposal for {product}",
            f"{product} in Japan — a quick recap",
            f"Following up: launching {product} in Japan",
        ]
        recap = (
            f"Following up once more on {product}. To recap: we help overseas creators "
            "launch in Japan on Makuake / GreenFunding, handling localization, "
            "marketing, and logistics so the process is smooth for you."
        )
        angle = f"\n\n{japan}" if japan else ""
        para = (
            f"{recap}{angle}\n\n"
            "Would a short 15-minute call next week work to explore a Japan launch? "
            "I can also send a one-page overview if that's easier."
        )
        jp_summary = f"{FOLLOWUP_STAGE_LABELS[stage]}：価値を要約して再提案し、短い打ち合わせ/資料送付を提案。"
    else:  # final
        subject_options = [
            f"Final note on {product} × Japan",
            f"Closing the loop on {product}",
            f"One last note about {product} in Japan",
        ]
        para = (
            "I don't want to crowd your inbox, so this will be my last note for now "
            f"regarding a Japan launch of {product}.\n\n"
            f"If bringing {product} to Japanese backers is something you'd like to "
            "revisit down the road, just reply anytime — I'd be glad to pick things up. "
            "Wishing you continued success."
        )
        jp_summary = f"{FOLLOWUP_STAGE_LABELS[stage]}：低圧力で締めつつ、いつでも再開できる余地を残す最終フォロー。"

    body = f"{greeting}\n\n{para}\n\n{sign}"
    return {
        "stage": stage,
        "stage_label": FOLLOWUP_STAGE_LABELS[stage],
        "subject": subject_options[0],
        "subject_options": subject_options,
        "body": body,
        "japanese_summary": jp_summary,
    }


def gmail_compose_url(
    to: str | None, subject: str, body: str
) -> str:
    """Gmail の新規作成画面を宛先・件名・本文入りで開く URL（OAuth 不要）。"""
    params = [
        ("view", "cm"),
        ("fs", "1"),
        ("su", subject),
        ("body", body),
    ]
    if to and to.strip():
        params.insert(2, ("to", to.strip()))
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params)
    return f"https://mail.google.com/mail/?{query}"


__all__ = [
    "FOLLOWUP_STAGES",
    "FOLLOWUP_STAGE_LABELS",
    "STAGE_TO_LEVEL",
    "stage_for_days",
    "build_followup_message",
    "gmail_compose_url",
]
