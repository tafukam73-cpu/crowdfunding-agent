"""Ulule 案件向けの評価シグナル。

Ulule（フランス発）は サステナブル / エコ / SDGs / ヨーロッパらしい洗練された
アパレル・小物・キッチンウェア・インテリア雑貨・ライフスタイル商品に強い。
日本のクラファン（Makuake / GreenFunding）との相性を測るため、優先キーワードを
評価に反映する。モック評価器・Claude 評価器の双方が利用する。
"""
from __future__ import annotations

from app.models.project import Project, SourceSite
from app.util.text import html_to_text

# 高評価キーワード（サステナブル・ヨーロッパデザイン・ライフスタイル）
HIGH_KEYWORDS = (
    "sustainable", "eco", "ethical", "recycled", "reusable", "zero waste",
    "organic", "responsible", "made in france", "made in europe", "artisan",
    "design", "lifestyle", "kitchen", "home", "interior", "furniture",
    "fashion", "bag", "leather", "textile", "travel", "kids", "pet", "wellness",
)
# 低優先 / 営業対象外寄りキーワード（寄付・文化・観光・イベント・団体支援など）
LOW_KEYWORDS = (
    "movie", "music", "book", "theater", "exhibition", "festival", "charity",
    "donation", "nonprofit", "political", "education only", "art project only",
    "tourism", "ecotourism", "association", "documentary", "humanitarian",
)

# --- 営業対象（商品性）判定用キーワード ---------------------------------------
# 営業対象外（非商品：寄付/文化活動/観光支援/イベント/映画/音楽/本/NPO・団体支援）。
# Ulule はフランス発のため仏語の語も併記する。
NON_PRODUCT_KEYWORDS = (
    # 寄付・NPO・団体支援
    "charity", "charitable", "donation", "donate", "fundraiser", "fundraising",
    "nonprofit", "non-profit", "non profit", "ngo", "association", "associative",
    "solidarity", "solidaire", "humanitarian", "humanitaire", "philanthropy",
    "community support", "communauté", "local development",
    # 観光・地域支援
    "tourism", "tourisme", "ecotourism", "eco-tourism", "ecotour", "tourist",
    "touristique", "village", "reforestation", "wildlife", "biodiversity",
    # イベント・文化活動
    "festival", "exhibition", "exposition", "spectacle", "concert",
    # 音楽・映画・本・演劇・教育・文化
    "music", "musique", "album", "movie", "film", "cinema", "documentary",
    "documentaire", "book", "novel", "livre", "comic", "magazine",
    "theater", "theatre", "dance", "opera", "education", "school", "scolaire",
    "cultural project", "patrimoine", "heritage",
)

# 営業対象（物販：アパレル/バッグ/小物/キッチン/インテリア/雑貨/サステナブル/
# エコ/デザイン/ペット/美容・健康商品 など）。
PRODUCT_KEYWORDS = (
    # アパレル・靴
    "apparel", "clothing", "clothes", "fashion", "wear", "t-shirt", "tshirt",
    "shirt", "jacket", "coat", "denim", "knit", "sneaker", "shoes", "boots",
    # バッグ・小物・アクセサリー
    "bag", "backpack", "tote", "wallet", "purse", "luggage", "accessory",
    "accessories", "jewel", "jewelry", "jewellery", "watch", "scarf",
    # キッチン・テーブルウェア
    "kitchen", "cookware", "tableware", "mug", "bottle", "cutlery", "knife",
    # インテリア・家具・雑貨
    "interior", "furniture", "homeware", "decor", "deco", "lamp", "candle",
    "ceramic", "pottery", "stationery", "notebook", "goods", "gadget",
    # サステナブル・エコ・デザイン
    "sustainable", "eco-friendly", "eco friendly", "recycled", "reusable",
    "organic", "zero waste", "design", "designer",
    # ペット
    "pet", "dog", "cat",
    # 美容・健康
    "beauty", "cosmetic", "cosmetics", "skincare", "makeup", "wellness",
    "health", "fragrance", "perfume",
    # 素材・その他物販
    "leather", "textile", "wool", "linen", "fabric", "toy", "outdoor",
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
# クラフトマンシップ（職人技）を示す語
_CRAFT = (
    "artisan", "handmade", "hand-made", "handcrafted", "craft", "leather",
    "textile", "ceramic", "wood", "woodwork", "made in france", "atelier",
    "knit", "weav",
)
# ギフト性を示す語
_GIFT = (
    "gift", "accessory", "accessories", "jewel", "candle", "kids", "pet",
    "home", "kitchen", "design", "stationery", "deco",
)
# 取得結果メモの開始マーカー（キーワード判定からは除外する）
MEMO_MARKER = "[Ulule]"


def is_ulule(project: Project) -> bool:
    return getattr(project, "source_site", None) in (
        SourceSite.ulule, SourceSite.ulule.value
    )


def _text(project: Project) -> str:
    """評価キーワード判定用テキスト（取得メモ部分は除外して誤検出を防ぐ）。"""
    desc = project.description or ""
    # メモ（[Ulule] 以降の判断材料）はキーワード検出に含めない
    idx = desc.find(MEMO_MARKER)
    if idx >= 0:
        desc = desc[:idx]
    return " ".join(
        str(x or "") for x in (project.title, desc, project.category)
    ).lower()


def clean_description(description: str | None, *, max_len: int = 1500) -> str | None:
    """description から HTML を除去した読みやすい概要を返す。

    取得メモ（`[Ulule]` 以降）はプレーンテキストなので保持し、その手前の本文だけ
    HTML 除去・トリムする。UI 表示用。
    """
    if not description:
        return description
    idx = description.find(MEMO_MARKER)
    body = description[:idx] if idx >= 0 else description
    memo = description[idx:].strip() if idx >= 0 else ""
    body_clean = html_to_text(body, max_len=max_len) or ""
    if memo:
        return (body_clean + "\n\n" + memo).strip() if body_clean else memo
    return body_clean or None


def _kw_hits(text: str, words: tuple[str, ...]) -> list[str]:
    return sorted({w for w in words if w in text})


def product_assessment(project: Project) -> dict:
    """Ulule 案件の商品性・営業対象判定を決定的に算出する。

    - physical_product_score: 物販（フィジカルプロダクト）らしさ（0〜100）
    - sales_target_score: 営業対象としての優先度（0〜100）
    - is_sales_target_candidate: 営業対象候補か（非商品が支配的なら False）

    判定はキーワード（メモ除外済みテキスト＋カテゴリ）に基づく。非商品語のみ
    （商品語が無い）の場合に「営業対象外の可能性あり」とする。
    """
    hay = _text(project) + " " + (project.category or "").lower()
    p_hits = _kw_hits(hay, PRODUCT_KEYWORDS)
    n_hits = _kw_hits(hay, NON_PRODUCT_KEYWORDS)

    physical = _clamp(min(100, 22 * len(p_hits)) - 15 * len(n_hits) + (20 if p_hits else 0))
    # 非商品語があり、かつ商品語が一つも無ければ営業対象外（寄付/観光/文化など）
    is_candidate = not (bool(n_hits) and not p_hits)
    if is_candidate:
        sales = _clamp(physical - 18 * len(n_hits))
    else:
        sales = _clamp(15 - 5 * len(n_hits))

    return {
        "physical_product_score": physical,
        "sales_target_score": sales,
        "is_sales_target_candidate": is_candidate,
        "product_hits": p_hits,
        "non_product_hits": n_hits,
    }


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


def _clamp(v: float, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(round(v))))


def _funded_pct(project: Project) -> float:
    try:
        g = float(project.goal_amount or 0)
        r = float(project.raised_amount or 0)
        return (r / g * 100) if g else 0.0
    except (TypeError, ValueError):
        return 0.0


# 評価軸（Ulule 用の追加キー）。既存 8 軸とは別に axis_scores へ追加する。
ULULE_AXIS_KEYS = [
    "europe_design_score",
    "sustainability_score",
    "craftsmanship_score",
    "gift_potential_score",
    "japan_lifestyle_fit_score",
    "premium_brand_potential_score",
]


def ulule_axis_scores(project: Project) -> dict[str, int]:
    """Ulule 案件向けの 6 スコア（0〜100）を決定的に算出する。

    キーワード（メモ除外済みテキスト）＋資金実績から推定。既存 8 軸とは独立で、
    総合スコアには影響させない（情報提供用に axis_scores へ追加保存する）。
    """
    text = _text(project)
    currency = (project.currency or "").upper()
    european_cur = currency in ("EUR", "GBP", "CHF", "SEK", "DKK", "NOK")
    pct = _funded_pct(project)
    backers = project.backers_count or 0

    def hits(words: tuple[str, ...]) -> int:
        return sum(1 for w in words if w in text)

    europe = 40 + (25 if european_cur else 0) + min(35, 12 * hits(_EUROPE))
    sustain = 15 + min(85, 22 * hits(_SUSTAIN))
    craft = 20 + min(80, 20 * hits(_CRAFT))
    gift = 20 + min(70, 16 * hits(_GIFT))
    jp = 20 + min(80, 18 * hits(_LIFESTYLE))
    # プレミアム性：達成率・支援者数・デザイン/職人性から
    premium = 30
    if pct >= 300:
        premium += 35
    elif pct >= 150:
        premium += 20
    elif pct >= 100:
        premium += 10
    if backers >= 1000:
        premium += 15
    elif backers >= 300:
        premium += 8
    premium += min(20, 8 * hits(_EUROPE))

    return {
        "europe_design_score": _clamp(europe),
        "sustainability_score": _clamp(sustain),
        "craftsmanship_score": _clamp(craft),
        "gift_potential_score": _clamp(gift),
        "japan_lifestyle_fit_score": _clamp(jp),
        "premium_brand_potential_score": _clamp(premium),
    }


# axis キー → 日本語ラベル（UI / reasons 用）
ULULE_AXIS_LABELS = {
    "europe_design_score": "Europe Design",
    "sustainability_score": "Sustainability",
    "craftsmanship_score": "Craftsmanship",
    "gift_potential_score": "Gift Potential",
    "japan_lifestyle_fit_score": "Japan Lifestyle Fit",
    "premium_brand_potential_score": "Premium Brand Potential",
}


def reason_line(sig: dict) -> str:
    """ai_evaluations.reasons へ入れる Ulule 観点の理由文（キーワード観点）。"""
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


def assessment_from_text(text: str, funded_pct: float = 0.0) -> dict[str, bool]:
    """テキスト＋達成率から Ulule の判断材料（6 観点）の該当有無を返す。

    スクレイパーが取得結果のメモ（[Ulule] 以降）へ残すために使う。
    """
    t = (text or "").lower()
    return {
        "Europe Design": any(k in t for k in _EUROPE),
        "Sustainability": any(k in t for k in _SUSTAIN),
        "Craftsmanship": any(k in t for k in _CRAFT),
        "Gift Potential": any(k in t for k in _GIFT),
        "Japan Lifestyle Fit": any(k in t for k in _LIFESTYLE),
        "Premium Brand Potential": funded_pct >= 150
        or any(k in t for k in ("premium", "luxury", "made in france")),
    }


def reason_text(project: Project) -> str:
    """reasons へ入れる Ulule 観点（キーワード＋6スコア＋商品性）。両エンジン共通。"""
    sig = signals(project)
    scores = ulule_axis_scores(project)
    pa = product_assessment(project)
    score_str = " / ".join(
        f"{ULULE_AXIS_LABELS[k]} {scores[k]}" for k in ULULE_AXIS_KEYS
    )
    line = reason_line(sig) + "\n・Ululeスコア: " + score_str
    line += (
        f"\n・商品性: physical {pa['physical_product_score']} / "
        f"sales {pa['sales_target_score']}"
    )
    if not pa["is_sales_target_candidate"]:
        line += (
            "\n・営業対象外の可能性あり（寄付/観光/文化活動/イベント/団体支援など"
            "の非商品プロジェクトと推定）"
        )
    return line


def prompt_block(project: Project) -> str:
    """Claude 評価プロンプトへ付与する Ulule 観点の指示ブロック。"""
    sig = signals(project)
    pa = product_assessment(project)
    lines = [
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
        "",
        "# 営業対象判定（重要）",
        "当社の営業対象は『日本で物販できる商品』です。寄付・文化活動・観光支援・"
        "イベント・映画・音楽・本・NPO/団体支援などの非商品プロジェクトは"
        "営業対象外（低評価）としてください。",
        f"検出された高評価キーワード: {', '.join(sig['high_hits']) or '（なし）'}",
        f"検出された低優先キーワード: {', '.join(sig['low_hits']) or '（なし）'}",
        f"商品性スコア（physical={pa['physical_product_score']} / "
        f"sales={pa['sales_target_score']}）",
    ]
    if not pa["is_sales_target_candidate"]:
        lines.append(
            "※この案件は非商品（営業対象外）の可能性が高いと推定されています。"
            "営業すべきか・総合スコアは低めに評価してください。"
        )
    return "\n".join(lines)
