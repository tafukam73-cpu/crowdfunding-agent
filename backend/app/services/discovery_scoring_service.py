"""AI Discovery Scoring Engine（Discovery Engine v1-2）。

発掘商品候補（DiscoveredProduct または商品情報 dict）を受け取り、日本の
クラウドファンディング（Makuake / GREEN FUNDING 等）向きかどうかを多軸で
評価する。

設計方針：
- 実 API キー（OpenAI/Claude 等）が無くても必ず動く。AI 呼び出し部分は
  ``ai_fn`` として注入でき（mock 可能）、未指定・失敗・不正 JSON のときは
  ルールベース評価にフォールバックする。
- 各スコアは 0〜100 に正規化。None や不正値が来ても安全側の既定値へ丸める。
- スコアの意味は「高いほど日本 CF 参入に有利」で統一する。リスク系スコア
  （regulatory_risk_score 等）も *高い=リスクが低い（安全）* とする。よって
  医療・食品・無線・危険物などの要注意カテゴリでは regulatory_risk_score は
  低くなる。

出力は DB（discovered_products）のスコア系カラムと同じキーを持つ dict：
  japan_fit_score / crowdfunding_fit_score / novelty_score / logistics_score /
  regulatory_risk_score / competition_risk_score / japan_entry_risk_score /
  overall_discovery_score / discovery_reasoning / recommended_next_action
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger("discovery_scoring")

# スコア系カラム（0〜100 の整数）
SCORE_FIELDS = (
    "japan_fit_score",
    "crowdfunding_fit_score",
    "novelty_score",
    "logistics_score",
    "regulatory_risk_score",
    "competition_risk_score",
    "japan_entry_risk_score",
    "overall_discovery_score",
)
# テキスト系カラム
_TEXT_FIELDS = ("discovery_reasoning", "recommended_next_action")

RULE_ENGINE_NAME = "rule-based-discovery-v1"
AI_ENGINE_NAME = "ai-discovery-v1"

# --- カテゴリ判定用キーワード（category / 商品名 / タイトル / 説明文を横断検索） ---
# 高評価（小型・軽量・日用品系）。canonical -> マッチする部分文字列。
_HIGH_FIT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "small gadget": ("small gadget", "gadget", "mini ", "compact", "小型", "ガジェット"),
    "kitchen": ("kitchen", "cook", "chef", "utensil", "キッチン", "調理"),
    "storage": ("storage", "organizer", "organiser", "container", "収納"),
    "outdoor": ("outdoor", "camping", "camp ", "hiking", "アウトドア", "キャンプ"),
    "pet": ("pet", "dog", "cat", "ペット"),
    "stationery": ("stationery", "stationary", "notebook", "文具", "ステーショナリー"),
    "sleep": ("sleep", "pillow", "mattress", "bedding", "睡眠", "枕"),
    "relaxation": ("relax", "massage", "wellness", "リラックス", "マッサージ"),
    "sustainable": ("sustainable", "eco-", "eco ", "reusable", "recycl", "bamboo",
                    "サステナブル", "エコ"),
    "home goods": ("home goods", "household", "homeware", "living", "日用品", "生活雑貨"),
    "travel": ("travel", "luggage", "backpack", "トラベル", "旅行", "バックパック"),
    "design goods": ("design goods", "designer", "minimalist", "デザイン雑貨"),
}
# 低評価・要注意（規制・輸入・安全リスク）。canonical -> マッチ部分文字列。
_CAUTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "medical": ("medical", "medicine", "therapy", "clinical", "diagnos", "医療"),
    "supplement": ("supplement", "vitamin", "protein powder", "nutrition", "サプリ"),
    "food": ("food", "snack", "coffee", "beverage", "edible", "食品", "飲料"),
    "cosmetics": ("cosmetic", "skincare", "makeup", "beauty", "serum", "化粧品"),
    "wireless": ("wireless", "bluetooth", "wi-fi", "wifi", "無線", "technical standard"),
    "radio": ("radio", "transmitter", "transceiver", "電波"),
    "large battery": ("large battery", "power station", "power bank", "battery pack",
                      "lithium", "大型バッテリー", "モバイルバッテリー"),
    "children": ("children", "child ", "kids", "toddler", "baby", "infant", "子供", "幼児"),
    "knife": ("knife", "blade", "刃物", "ナイフ"),
    "weapon": ("weapon", "firearm", " gun", "taser", "pepper spray", "武器"),
    "chemical": ("chemical", "solvent", "pesticide", "化学", "薬品"),
    "alcohol": ("alcohol", "wine", "beer", "whisky", "whiskey", "liquor", "酒"),
    "nicotine": ("nicotine", "vape", "e-cigarette", "tobacco", "cigar", "ニコチン", "タバコ"),
}
# 技適 / PSE 等、物流・輸入面でも重いカテゴリ
_LOGISTICS_HEAVY = ("wireless", "radio", "large battery")

# 説明文がこの文字数未満なら「情報不足」とみなし confidence を下げる
_SHORT_DESC_THRESHOLD = 40

# 調達額の「大きさ」を通貨をまたいで比較するための概算 USD 換算レート（1 通貨単位＝USD）。
# 目的は KRW / TWD などの大きな額面を USD/JPY と誤解しないこと（厳密な為替ではない）。
# 未知の通貨・通貨不明は 1.0（USD 相当）として扱う。
_APPROX_USD_PER_UNIT: dict[str, float] = {
    "USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CAD": 0.73, "AUD": 0.66,
    "JPY": 0.0067, "KRW": 0.00075, "TWD": 0.031, "HKD": 0.128,
    "SGD": 0.74, "CHF": 1.1, "NZD": 0.6, "BRL": 0.18, "INR": 0.012,
}
# 「大型調達」とみなす USD 換算のしきい値（この額を超えると CF 適性を少し加点）。
_LARGE_FUNDING_USD = 100_000.0


def _funding_usd(amount: float | None, currency: Any) -> float | None:
    """調達額を概算 USD に換算する（通貨不明は 1.0 倍＝USD 相当）。"""
    if amount is None:
        return None
    code = str(getattr(currency, "value", currency) or "USD").strip().upper()
    return amount * _APPROX_USD_PER_UNIT.get(code, 1.0)


def _clamp(value: Any, default: int = 50) -> int:
    """0〜100 の整数に正規化する。None / 不正値は default に丸める。"""
    try:
        if value is None:
            return default
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(0, min(100, v))


# --- 営業価値スコア（Sales Value）------------------------------------------- #
# 「営業すべき順」を作るための合成スコア。日本市場適性・CF 適性・独自性・各種
# リスクの低さ（=安全度）を重み付けした基礎点に、クラファン実績（達成率・支援者数）
# の traction ボーナスを足す。既存のスコア系カラムから決定的に算出する（再計算可能）。
_SALES_VALUE_WEIGHTS: dict[str, float] = {
    "japan_fit_score": 0.30,          # 日本市場適性
    "crowdfunding_fit_score": 0.24,   # Makuake / GreenFunding 向き（BtoC/CF 適性）
    "novelty_score": 0.16,            # 商品の独自性
    "regulatory_risk_score": 0.10,    # 規制の安全度（高い=安全）
    "japan_entry_risk_score": 0.08,   # 日本参入の安全度（未上陸可能性を含む）
    "logistics_score": 0.07,          # 小型・軽量・物流適性
    "competition_risk_score": 0.05,   # 競合の少なさ（高い=安全）
}


def _read_score(product: Any, key: str) -> Any:
    if isinstance(product, dict):
        return product.get(key)
    return getattr(product, key, None)


def achievement_rate(product: Any) -> float | None:
    """達成率（%）= 調達額 / 目標額 × 100。目標額が無い/0 なら None。"""
    info = _extract_info(product)
    amount = _to_float(info.get("funding_amount"))
    goal = _to_float(info.get("funding_goal"))
    if amount is None or goal is None or goal <= 0:
        return None
    return round(amount / goal * 100.0, 1)


def _traction_bonus(product: Any) -> float:
    """クラファン実績（達成率・支援者数）による加点（最大 +18）。"""
    bonus = 0.0
    rate = achievement_rate(product)
    if rate is not None:
        if rate >= 1000:
            bonus += 15
        elif rate >= 300:
            bonus += 12
        elif rate >= 100:
            bonus += 8
        elif rate >= 50:
            bonus += 4
    backers_raw = _read_score(product, "backers_count")
    try:
        backers = int(backers_raw) if backers_raw is not None else None
    except (TypeError, ValueError):
        backers = None
    if backers is not None:
        if backers >= 10_000:
            bonus += 10
        elif backers >= 2_000:
            bonus += 8
        elif backers >= 500:
            bonus += 5
        elif backers >= 100:
            bonus += 2
    return min(bonus, 18.0)


def sales_value_score(product: Any) -> int | None:
    """営業価値スコア（0〜100）。未スコアリング（overall 未設定）なら None。

    基礎点（日本適性・CF 適性・独自性・安全度の重み付き平均）に、クラファン実績の
    traction ボーナスを加え、失敗/中止案件は減点する。「営業すべき順」の並び替えに使う。
    """
    if _read_score(product, "overall_discovery_score") is None:
        return None  # 未スコアリングは営業価値も未確定
    base = sum(
        _clamp(_read_score(product, key)) * weight
        for key, weight in _SALES_VALUE_WEIGHTS.items()
    )
    value = base + _traction_bonus(product)
    status_raw = _read_score(product, "status")
    status = str(getattr(status_raw, "value", status_raw) or "").lower()
    if status in ("failed", "canceled"):
        value -= 12  # 失敗・中止案件は営業価値を下げる
    return _clamp(value)


def _extract_info(product: Any) -> dict:
    """DiscoveredProduct / dict / None から評価に必要な項目を取り出す。"""
    if product is None:
        return {}
    keys = (
        "category", "product_name", "project_title", "description",
        "status", "backers_count", "funding_amount", "funding_goal", "currency",
    )
    if isinstance(product, dict):
        return {k: product.get(k) for k in keys}
    return {k: getattr(product, k, None) for k in keys}


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _match_categories(text: str, table: dict[str, tuple[str, ...]]) -> list[str]:
    return [name for name, kws in table.items() if any(k in text for k in kws)]


def _rule_based_scores(info: dict) -> dict:
    """ルールベースのフォールバック評価（ネットワーク・API 不要）。"""
    text = " ".join(
        str(info.get(k) or "")
        for k in ("category", "product_name", "project_title", "description")
    ).lower()

    high_hits = _match_categories(text, _HIGH_FIT_KEYWORDS)
    caution_hits = _match_categories(text, _CAUTION_KEYWORDS)

    # 中立の初期値（リスク系は「高い=安全」なので高めスタート）
    japan_fit = 50
    crowdfunding_fit = 50
    novelty = 50
    logistics = 55
    regulatory_risk = 75
    competition_risk = 55
    japan_entry_risk = 70

    # 高評価カテゴリ：小型・軽量・日用品は日本 CF / 物流と相性が良い
    if high_hits:
        japan_fit += 20 + 5 * min(len(high_hits), 3)
        crowdfunding_fit += 15
        logistics += 10

    # 要注意カテゴリ：規制・輸入リスク。regulatory_risk_score を下げる（=リスク大）
    if caution_hits:
        regulatory_risk -= 30 + 10 * min(len(caution_hits), 3)
        japan_entry_risk -= 20
        japan_fit -= 10

    # 技適 / PSE 等は物流・参入も重い
    if any(c in caution_hits for c in _LOGISTICS_HEAVY):
        logistics -= 20
        japan_entry_risk -= 10

    # 支援者数・調達額が大きいほど CF 実績・話題性を少し加点
    backers = info.get("backers_count")
    try:
        backers = int(backers) if backers is not None else None
    except (TypeError, ValueError):
        backers = None
    funding = _to_float(info.get("funding_amount"))

    if backers is not None:
        if backers >= 1000:
            crowdfunding_fit += 10
            novelty += 5
        if backers >= 5000:
            crowdfunding_fit += 5
            novelty += 5
    # 調達額は通貨をまたいで比較するため概算 USD 換算で判定する（KRW/TWD を USD と
    # 誤解して過大加点しない）。
    funding_usd = _funding_usd(funding, info.get("currency"))
    if funding_usd is not None and funding_usd >= _LARGE_FUNDING_USD:
        crowdfunding_fit += 5

    # 説明文が短すぎる場合は confidence を下げる（overall を減点し reasoning に明記）
    desc = str(info.get("description") or "").strip()
    short_desc = len(desc) < _SHORT_DESC_THRESHOLD

    scores = {
        "japan_fit_score": _clamp(japan_fit),
        "crowdfunding_fit_score": _clamp(crowdfunding_fit),
        "novelty_score": _clamp(novelty),
        "logistics_score": _clamp(logistics),
        "regulatory_risk_score": _clamp(regulatory_risk),
        "competition_risk_score": _clamp(competition_risk),
        "japan_entry_risk_score": _clamp(japan_entry_risk),
    }

    # 総合スコア（好適要素を加点、リスク要素は「安全度」として加点する重み付き平均）
    overall = (
        scores["japan_fit_score"] * 0.30
        + scores["crowdfunding_fit_score"] * 0.25
        + scores["novelty_score"] * 0.15
        + scores["logistics_score"] * 0.10
        + scores["regulatory_risk_score"] * 0.10
        + scores["competition_risk_score"] * 0.05
        + scores["japan_entry_risk_score"] * 0.05
    )
    if short_desc:
        overall -= 5
    scores["overall_discovery_score"] = _clamp(overall)

    scores["discovery_reasoning"] = _build_reasoning(
        high_hits, caution_hits, backers, funding, short_desc
    )
    scores["recommended_next_action"] = _build_next_action(
        scores["overall_discovery_score"], caution_hits
    )
    return scores


def _build_reasoning(
    high_hits: list[str], caution_hits: list[str],
    backers: int | None, funding: float | None, short_desc: bool,
) -> str:
    parts: list[str] = []
    if high_hits:
        parts.append(
            "日本 CF・物流と相性の良いカテゴリ（"
            + " / ".join(high_hits) + "）に該当。"
        )
    if caution_hits:
        parts.append(
            "規制・輸入で注意が必要なカテゴリ（"
            + " / ".join(caution_hits) + "）を含み、規制リスクを高く見積もった。"
        )
    if not high_hits and not caution_hits:
        parts.append("カテゴリからは明確な適合/リスク要因を検出できず、中立的に評価した。")
    if backers is not None and backers >= 1000:
        parts.append(f"支援者数 {backers:,} 人と実績があり、話題性・CF 適性を加点した。")
    if funding is not None and funding >= 100_000:
        parts.append("調達額が大きく、市場性の裏付けがある。")
    if short_desc:
        parts.append("説明文が短く情報が不足しているため、確信度は低め（要追加調査）。")
    return " ".join(parts)


def _build_next_action(overall: int, caution_hits: list[str]) -> str:
    if overall >= 70:
        action = (
            "優先アプローチ。日本のクラウドファンディング（Makuake / GREEN FUNDING）"
            "掲載を前提に、メーカーへ独占販売・日本展開を打診する。"
        )
    elif overall >= 45:
        action = (
            "有望。日本未上陸か・競合状況・規制を確認したうえでアプローチを検討する。"
        )
    else:
        action = "現時点では優先度低。規制・競合リスクを精査し、保留候補とする。"
    if caution_hits:
        action += "（規制対応：" + " / ".join(caution_hits) + " の許認可・輸入要件を要確認）"
    return action


def _parse_ai_json(raw: Any) -> dict:
    """AI 応答（dict / JSON 文字列）を dict に変換する。壊れていれば例外。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ValueError("AI 応答の型が不正です")
    text = raw.strip()
    # ```json ... ``` のコードフェンスを除去
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    # 最初の { から最後の } までを抽出（前後の説明文を許容）
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("AI 応答に JSON オブジェクトが見つかりません")
    return json.loads(text[start:end + 1])


def _scores_from_ai(data: dict, info: dict) -> dict:
    """AI が返した dict を正規化してスコア dict に落とす（欠損はルール値で補完）。"""
    fallback = _rule_based_scores(info)
    result: dict = {}
    for field in SCORE_FIELDS:
        result[field] = _clamp(data.get(field), default=fallback[field])
    reasoning = data.get("discovery_reasoning") or data.get("reasoning")
    action = data.get("recommended_next_action") or data.get("next_action")
    result["discovery_reasoning"] = (
        str(reasoning) if reasoning else fallback["discovery_reasoning"]
    )
    result["recommended_next_action"] = (
        str(action) if action else fallback["recommended_next_action"]
    )
    return result


def score(
    product: Any,
    *,
    ai_fn: Callable[[dict], Any] | None = None,
) -> dict:
    """商品候補を評価してスコア dict を返す。

    ai_fn が指定されれば AI 評価を試み、未指定・例外・不正 JSON のときは
    ルールベースにフォールバックする。いずれの経路でも 0〜100 に正規化された
    全スコアと reasoning / next_action を返す（例外は投げない）。
    """
    info = _extract_info(product)

    if ai_fn is not None:
        try:
            raw = ai_fn(info)
            data = _parse_ai_json(raw)
            return _scores_from_ai(data, info)
        except Exception as exc:  # noqa: BLE001  AI 失敗はフォールバックで吸収
            logger.warning("discovery AI scoring failed, falling back to rules: %s", exc)

    return _rule_based_scores(info)
