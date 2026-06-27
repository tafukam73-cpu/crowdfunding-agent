"""Ulule 案件向けの評価シグナル。

Ulule（フランス発）は サステナブル / エコ / SDGs / ヨーロッパらしい洗練された
アパレル・小物・キッチンウェア・インテリア雑貨・ライフスタイル商品に強い。
日本のクラファン（Makuake / GreenFunding）との相性を測るため、優先キーワードを
評価に反映する。モック評価器・Claude 評価器の双方が利用する。
"""
from __future__ import annotations

from app.models.project import Project, SourceSite

# 高評価キーワード（サステナブル・ヨーロッパデザイン・ライフスタイル）
HIGH_KEYWORDS = (
    "sustainable", "eco", "ethical", "recycled", "reusable", "zero waste",
    "organic", "responsible", "made in france", "made in europe", "artisan",
    "design", "lifestyle", "kitchen", "home", "interior", "furniture",
    "fashion", "bag", "leather", "textile", "travel", "kids", "pet", "wellness",
)
# 低優先 / 営業対象外寄りキーワード
LOW_KEYWORDS = (
    "movie", "music", "book", "theater", "exhibition", "festival", "charity",
    "donation", "nonprofit", "political", "education only", "art project only",
)

# サステナブル性を示す語
_SUSTAIN = (
    "sustainable", "eco", "ethical", "recycled", "reusable", "zero waste",
    "organic", "responsible",
)
# ヨーロッパ/デザイン性を示す語
_EUROPE = ("made in france", "made in europe", "artisan", "design", "european")
# 日本のライフスタイル/ギフト適性を示す語
_LIFESTYLE = (
    "lifestyle", "kitchen", "home", "interior", "furniture", "fashion", "bag",
    "leather", "textile", "travel", "kids", "pet", "wellness", "gift",
)


def is_ulule(project: Project) -> bool:
    return getattr(project, "source_site", None) in (
        SourceSite.ulule, SourceSite.ulule.value
    )


def _text(project: Project) -> str:
    return " ".join(
        str(x or "")
        for x in (project.title, project.description, project.category)
    ).lower()


def signals(project: Project) -> dict:
    """Ulule 案件の評価材料を返す。"""
    text = _text(project)
    high_hits = [k for k in HIGH_KEYWORDS if k in text]
    low_hits = [k for k in LOW_KEYWORDS if k in text]
    return {
        "is_ulule": is_ulule(project),
        "high_hits": high_hits,
        "low_hits": low_hits,
        "sustainability": any(k in text for k in _SUSTAIN),
        "europe_design": any(k in text for k in _EUROPE),
        "lifestyle_fit": any(k in text for k in _LIFESTYLE),
    }


def reason_line(sig: dict) -> str:
    """ai_evaluations.reasons へ入れる Ulule 観点の理由文。"""
    bits = []
    bits.append("サステナブル性: " + ("高" if sig["sustainability"] else "要確認"))
    bits.append("ヨーロッパデザイン性: " + ("高" if sig["europe_design"] else "標準"))
    bits.append("日本市場（Makuake/GreenFunding）との相性: "
                + ("良好" if sig["lifestyle_fit"] else "要検討"))
    if sig["high_hits"]:
        bits.append("注目キーワード: " + ", ".join(sig["high_hits"][:6]))
    if sig["low_hits"]:
        bits.append("留意（営業対象外寄り）: " + ", ".join(sig["low_hits"][:4]))
    return "Ulule案件として、" + " / ".join(bits)


def prompt_block(project: Project) -> str:
    """Claude 評価プロンプトへ付与する Ulule 観点の指示ブロック。"""
    sig = signals(project)
    return "\n".join([
        "",
        "# Ulule 案件としての追加観点（reasons に反映してください）",
        "Ulule はフランス発で、サステナブル/エコ/ヨーロッパらしい洗練された "
        "アパレル・小物・キッチンウェア・インテリア雑貨・ライフスタイル商品に強い。",
        "次の観点で日本市場適性を評価し、reasons に具体的に書いてください：",
        "- Europe Design Fit（ヨーロッパらしいデザイン性）",
        "- Sustainability Fit（サステナブル/エコ/エシカル性）",
        "- Japanese Lifestyle Fit（日本のライフスタイルへの馴染み）",
        "- Gift Potential（ギフト需要）",
        "- Interior / Kitchen / Fashion suitability",
        "- Makuake向きか / GreenFunding向きか",
        f"検出された高評価キーワード: {', '.join(sig['high_hits']) or '（なし）'}",
        f"検出された低優先キーワード: {', '.join(sig['low_hits']) or '（なし）'}",
    ])
