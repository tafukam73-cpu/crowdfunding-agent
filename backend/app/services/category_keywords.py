"""日本市場適性・輸入規制の判定に使うカテゴリ横断キーワード。

Contact Intelligence の日本クラファン適性ゲート（contact_search_gate）と、
（撤去予定の）商品発掘スコアリングの両方から参照される中立の共通定義。
商品発掘（Discovery Engine）の撤去後も Contact Intelligence が壊れないよう、
キーワード定義をここへ集約する。

canonical カテゴリ名 -> マッチする部分文字列（小文字化したテキストに対して判定）。
"""
from __future__ import annotations

from typing import Any

# 高評価（小型・軽量・日用品系）。日本クラファンで受けやすい物販カテゴリ。
HIGH_FIT_KEYWORDS: dict[str, tuple[str, ...]] = {
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

# 低評価・要注意（規制・輸入・安全リスク）。
CAUTION_KEYWORDS: dict[str, tuple[str, ...]] = {
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


# 技適 / PSE 等、物流・輸入面でも重いカテゴリ（CAUTION_KEYWORDS の一部）。
LOGISTICS_HEAVY: tuple[str, ...] = ("wireless", "radio", "large battery")


def match_categories(text: str, table: dict[str, tuple[str, ...]]) -> list[str]:
    """text（小文字化済み想定）に一致する canonical カテゴリ名の一覧を返す。"""
    return [name for name, kws in table.items() if any(k in text for k in kws)]


def clamp(value: Any, default: int = 50) -> int:
    """0〜100 の整数に正規化する。None / 不正値は default に丸める。"""
    try:
        if value is None:
            return default
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(0, min(100, v))
