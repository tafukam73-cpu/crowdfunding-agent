"""営業メールのパーソナライズ材料（personalization_context）を組み立てる。

メール生成の前段で、案件（project）の各フィールド
（title / description / category / currency / raised_amount / backers_count /
goal_amount / video_url / image_url / maker_name / source_site）から、
商品・メーカーごとに変化する個別化材料をロジックで作る。モック・Claude の
双方がこれを利用し、固定テンプレートではなく案件ごとに異なる本文・称賛文を作る。

返す dict のキー（要件 4）:
  - product_name
  - key_features
  - impressive_points
  - japan_market_angle
  - maker_appeal
  - recommended_opening_sentence
  - personalized_compliment
  - product_highlights  （UI 表示用。注目ポイントの要約リスト）
"""
from __future__ import annotations

import re

from app.ai.prompts import emphasis_for
from app.models.project import Project

_PLATFORM_LABELS = {
    "kickstarter": "Kickstarter",
    "indiegogo": "Indiegogo",
    "wadiz": "Wadiz",
    "makuake": "Makuake",
    "greenfunding": "GreenFunding",
}

# カテゴリ別の「作り込み」を表す語（称賛文をカテゴリごとに変える）
_CATEGORY_FUNCTIONAL = {
    "ガジェット": "thoughtful engineering and everyday usability",
    "テック": "thoughtful engineering and everyday usability",
    "オーディオ": "refined sound quality and design",
    "アウトドア": "a rugged yet compact design",
    "キャンプ": "a rugged yet compact design",
    "ペット": "everyday practicality and a focus on safety",
    "美容": "a wellness-focused, daily-use design",
    "健康": "a wellness-focused, daily-use design",
    "ヘルス": "a wellness-focused, daily-use design",
}
_DEFAULT_FUNCTIONAL = "practical functionality"


def platform_label(project: Project) -> str:
    return _PLATFORM_LABELS.get(project.source_site, "your crowdfunding campaign")


def _sentences(text: str | None) -> list[str]:
    if not text:
        return []
    parts = re.split(r"(?<=[.!?。！？])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _funding_rate(project: Project) -> int | None:
    if not project.goal_amount or not project.raised_amount:
        return None
    try:
        return int(round(float(project.raised_amount) / float(project.goal_amount) * 100))
    except (ZeroDivisionError, ValueError):
        return None


def _money(project: Project) -> str | None:
    if not project.raised_amount:
        return None
    currency = project.currency or "USD"
    try:
        return f"{currency} {int(project.raised_amount):,}"
    except (ValueError, TypeError):
        return None


def _functional_word(category: str | None) -> str:
    if category:
        for key, phrase in _CATEGORY_FUNCTIONAL.items():
            if key in category:
                return phrase
    return _DEFAULT_FUNCTIONAL


def _key_features(project: Project) -> list[str]:
    """商品説明・カテゴリから商品の特徴を短文で抽出する（案件ごとに変化）。"""
    feats: list[str] = []
    for s in _sentences(project.description)[:2]:
        feats.append(s if len(s) <= 140 else s[:137].rstrip() + "…")
    if not feats:
        cat = project.category or "crowdfunding"
        feats.append(f"a {cat} product with a clear, focused concept")
    return feats


def _impressive_points(project: Project) -> list[str]:
    """クラファン実績・ビジュアルなど「支持されそうな理由」を作る。"""
    platform = platform_label(project)
    pts: list[str] = []
    money = _money(project)
    backers = project.backers_count
    if money and backers:
        pts.append(f"{money} raised from {backers:,} backers on {platform}")
    elif money:
        pts.append(f"{money} raised on {platform}")
    elif backers:
        pts.append(f"{backers:,} backers on {platform}")

    rate = _funding_rate(project)
    if rate and rate >= 100:
        pts.append(f"{rate:,}% of its funding goal reached")

    if project.video_url:
        pts.append("a product video that makes the concept easy to grasp")
    elif project.image_url:
        pts.append("a strong, clear visual presentation")

    if not pts:
        pts.append(f"genuine early traction on {platform}")
    return pts


def _japan_market_angle(project: Project) -> str:
    emphasis = emphasis_for(project.category)
    return (
        f"Japanese early adopters tend to value {emphasis}, which aligns well with "
        f"{project.title}."
    )


def _maker_appeal(project: Project) -> str:
    maker = project.maker_name or "your team"
    platform = platform_label(project)
    return (
        f"{maker}'s approach to turning a clear product insight into a compelling "
        f"{platform} campaign suggests a partner we would be excited to work with in "
        "Japan."
    )


def _opening_sentence(project: Project) -> str:
    platform = platform_label(project)
    return (
        f"I recently came across {project.title} on {platform}, and it immediately "
        "caught my attention."
    )


def _visual_clause(project: Project) -> str:
    if project.video_url:
        return "a clear visual story that backers can grasp quickly"
    if project.image_url:
        return "a strong visual presentation"
    return "a clear, easy-to-understand value proposition"


def _funding_clause(project: Project) -> str:
    rate = _funding_rate(project)
    if rate and rate >= 300:
        return ", as its standout funding result reflects"
    if project.backers_count:
        return ", something its backers clearly responded to"
    return ""


def _personalized_compliment(project: Project) -> str:
    """商品ごとに変わる具体的な称賛の一文（固定文ではない）。"""
    functional = _functional_word(project.category)
    visual = _visual_clause(project)
    funding = _funding_clause(project)
    return (
        f"We were particularly impressed by how {project.title} pairs {functional} "
        f"with {visual}{funding}."
    )


def _dedupe(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= limit:
            break
    return out


def build_personalization(project: Project) -> dict:
    """案件から個別化材料 dict を作る（モック・Claude 共通の前処理）。"""
    key_features = _key_features(project)
    impressive_points = _impressive_points(project)
    return {
        "product_name": project.title,
        "key_features": key_features,
        "impressive_points": impressive_points,
        "japan_market_angle": _japan_market_angle(project),
        "maker_appeal": _maker_appeal(project),
        "recommended_opening_sentence": _opening_sentence(project),
        "personalized_compliment": _personalized_compliment(project),
        # UI 表示用：注目ポイントの要約（実績＋特徴を統合・重複除去）
        "product_highlights": _dedupe(impressive_points + key_features, limit=5),
    }


def render_personalization_block(p: dict) -> str:
    """personalization 材料をプロンプト用のテキストブロックに整形する。"""
    lines = [
        "# Personalization material (use naturally as inspiration, do NOT copy verbatim)",
        f"Product name: {p.get('product_name', '')}",
        f"Key features: {'; '.join(p.get('key_features', []))}",
        f"Impressive points: {'; '.join(p.get('impressive_points', []))}",
        f"Japan market angle: {p.get('japan_market_angle', '')}",
        f"Maker appeal: {p.get('maker_appeal', '')}",
        f"Suggested opening: {p.get('recommended_opening_sentence', '')}",
        f"Suggested compliment: {p.get('personalized_compliment', '')}",
    ]
    return "\n".join(lines)
