"""Zeczec 詳細補完のオフライン検証（パース・非破壊更新・確度・理由）。

実ネットワーク不要。実測した詳細ページ構造を模した HTML fixture から、確認できた
事実だけを抽出し、既存 projects を非破壊で更新できることを検証する。

実行（backend ディレクトリで）:
    python tests/test_zeczec_enrichment.py
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.models.project import Project, SourceSite  # noqa: E402
from app.scrapers.zeczec_detail import (  # noqa: E402
    classify_external_link,
    looks_like_challenge,
    parse_detail,
)
from app.services import zeczec_enrichment_service as zes  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


# 実測した詳細ページ構造の最小再現（breadcrumb / 提案人 / og / 直リンク）。
_LIVE_HTML = """
<html><head>
<meta property="og:title" content="嘖嘖 | INOPRO 牙齒淨白貼片">
<meta property="og:description" content="美國 Amazon 銷售冠軍，14 天居家牙齒淨白方案！">
</head><body>
<div class="text-xs text-gray-500 mb-2 tracking-wider">
  <a class="text-gray-500 inline-block" href="/categories?type=1">預購式專案</a>
  <div class="inline-block mx-1">\\</div>
  <a class="text-gray-500 inline-block" href="/categories?category=18">科技</a>
</div>
<div class="text-sm text-gray-500">
  <span class="text-gray-500">提案人</span>
  <a class="font-bold" href="/users/3743542?tab=projects">MORESIE</a>
</div>
<div>1,941% 目標 NT$10,000 累計集資金額 NT$194,187 121 人 剩餘時間 21 天</div>
<a href="https://brandsite.example.tw/product">品牌官網</a>
<a href="https://reurl.cc/abc123">短縮リンク</a>
<a href="https://www.facebook.com/zeczec.com">zeczec fb</a>
</body></html>
"""

_FUNDED_HTML = """
<html><head>
<meta property="og:description" content="LG MoodMate 投影機・藍牙喇叭・氛圍燈">
</head><body>
<div class="text-xs text-gray-500 mb-2 tracking-wider">
  <a href="/categories?type=1">預購式專案</a>
  <a href="/categories?category=3">科技</a>
</div>
<div><span>提案人</span><a href="/users/2116435">LG</a></div>
<div>已於 2026/07/07 募資成功 27,783% 累計集資金額 NT$2,778,400</div>
<a href="https://www.lg.com/tw/projectors/pf600u/">官方產品頁</a>
</body></html>
"""

_CHALLENGE_HTML = (
    "<html><head><title>請稍候...</title></head><body>"
    "正在執行安全驗證 Cloudflare</body></html>"
)


def _proj(**kw) -> Project:
    base = dict(
        id=1, title="INOPRO 牙齒淨白貼片", source_site=SourceSite.zeczec.value,
        source_url="https://www.zeczec.com/projects/inopro",
        maker_name=None, maker_url=None, category=None, end_date=None,
        enrichment=None,
    )
    base.update(kw)
    return Project(**base)


def test_parse_live():
    print("test_parse_live")
    d = parse_detail(_LIVE_HTML, "提案人 MORESIE 剩餘時間 21 天")
    check("challenged=False", d["challenged"] is False)
    check("提案人=メーカー名", d["maker_name"] == "MORESIE")
    check("creator_url 正規化(?除去)",
          d["creator_url"] == "https://www.zeczec.com/users/3743542")
    check("カテゴリ=科技(category=)", d["category"] == "科技")
    check("project_type=預購式專案", d["project_type"] == "預購式專案")
    check("説明=og:description", "Amazon" in (d["description"] or ""))
    check("status=live", d["status"] == "live")
    check("end_date は live なので None", d["end_date"] is None)
    # 公式候補：直リンク=high、短縮=low、SNS/zeczec は除外
    cands = d["official_candidates"]
    highs = [c for c in cands if c["confidence"] == "high"]
    lows = [c for c in cands if c["confidence"] == "low"]
    check("直リンクは high 候補", any("brandsite.example.tw" in c["url"] for c in highs))
    check("短縮 URL は low 候補", any("reurl.cc" in c["url"] for c in lows))
    check("zeczec の SNS は候補に含めない",
          not any("facebook.com/zeczec" in c["url"] for c in cands))


def test_parse_funded():
    print("test_parse_funded")
    d = parse_detail(_FUNDED_HTML, "已於 2026/07/07 募資成功")
    check("status=funded", d["status"] == "funded")
    check("終了日=2026/07/07", d["end_date"] == date(2026, 7, 7))
    check("メーカー名=LG", d["maker_name"] == "LG")
    check("lg.com は high 候補",
          any("lg.com" in c["url"] and c["confidence"] == "high"
              for c in d["official_candidates"]))


def test_challenge_detection():
    print("test_challenge_detection")
    check("zh チャレンジ検出", looks_like_challenge("正在執行安全驗證"))
    check("en チャレンジ検出", looks_like_challenge("Just a moment..."))
    d = parse_detail(_CHALLENGE_HTML, "請稍候")
    check("チャレンジは challenged=True", d.get("challenged") is True)


def test_nondestructive_updates():
    print("test_nondestructive_updates")
    d = parse_detail(_LIVE_HTML, "剩餘時間 21 天")
    d["source_detail_url"] = "https://www.zeczec.com/projects/inopro"
    # 既存が空 → 補完される
    p = _proj()
    built = zes.build_enrichment_updates(p, d)
    cu = built["column_updates"]
    check("空のメーカー名は補完", cu.get("maker_name") == "MORESIE")
    check("空のカテゴリは補完", cu.get("category") == "科技")
    check("high 単一ドメインは maker_url に自動採用",
          cu.get("maker_url", "").startswith("https://brandsite.example.tw"))
    check("enrichment に creator_url を保存",
          built["enrichment"]["creator_url"].endswith("/users/3743542"))
    check("enrichment に商品説明を保存",
          "Amazon" in (built["enrichment"]["product_description"] or ""))
    check("enrichment に取得元 URL を保存",
          built["enrichment"]["source_detail_url"].endswith("/projects/inopro"))

    # 既存が非空 → 上書きしない（非破壊）
    p2 = _proj(maker_name="既存メーカー", category="既存カテゴリ",
               maker_url="https://existing.example.com")
    built2 = zes.build_enrichment_updates(p2, d)
    check("既存メーカー名は上書きしない", "maker_name" not in built2["column_updates"])
    check("既存カテゴリは上書きしない", "category" not in built2["column_updates"])
    check("既存 maker_url は上書きしない", "maker_url" not in built2["column_updates"])


def test_challenge_reason_recorded():
    print("test_challenge_reason_recorded")
    built = zes.build_enrichment_updates(_proj(), {"challenged": True})
    check("チャレンジ時は列更新なし", built["column_updates"] == {})
    check("チャレンジ理由を残す", "403" in built["reasons"].get("all", ""))


def test_multiple_high_not_autoselected():
    print("test_multiple_high_not_autoselected")
    d = {
        "challenged": False, "maker_name": "X", "creator_url": None,
        "category": None, "project_type": None, "description": None,
        "og_title": None, "status": None, "end_date": None,
        "official_candidates": [
            {"url": "https://a.example.com", "confidence": "high", "source": "zeczec_page_direct_link"},
            {"url": "https://b.example.org", "confidence": "high", "source": "zeczec_page_direct_link"},
        ],
        "socials": {},
    }
    built = zes.build_enrichment_updates(_proj(maker_name="X"), d)
    check("複数 high ドメインは自動採用しない", "maker_url" not in built["column_updates"])
    check("保留理由を残す", "複数" in built["reasons"]["official_site"])


def test_link_classification():
    print("test_link_classification")
    # 実 API 検証で判明した誤分類（ニュース記事・チャットを公式にしない）
    check("ブランド直リンクは high",
          classify_external_link("https://shop.unionchen.com.tw") == ("high", "zeczec_page_direct_link"))
    check("LG 製品ページは high",
          classify_external_link("https://www.lg.com/tw/projectors/pf600u/")[0] == "high")
    check("ニュース記事は low(media) に降格",
          classify_external_link("https://www.thenewslens.com/article/266807") == ("low", "zeczec_page_media_link"))
    check("messenger は除外", classify_external_link("https://www.messenger.com/t/100086443723620") is None)
    check("facebook は除外", classify_external_link("https://www.facebook.com/zeczec.com") is None)
    check("google forms は除外", classify_external_link("https://docs.google.com/forms/d/x") is None)
    check("記事パスは low に降格",
          classify_external_link("https://brand.example.com/news/launch")[0] == "low")


if __name__ == "__main__":
    test_parse_live()
    test_parse_funded()
    test_challenge_detection()
    test_nondestructive_updates()
    test_challenge_reason_recorded()
    test_multiple_high_not_autoselected()
    test_link_classification()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
