"""campaign_url の必須化と日本クラファン適性ゲートのオフライン検証（ネットワーク不要）。

検証内容:
  1. 各対応サイト（Kickstarter/Indiegogo/Wadiz/Zeczec）の案件に campaign_url が入る
  2. campaign_url と official_site_url が混同されない（公式サイトで代用しない）
  3. campaign_url なしの案件は自動メール探索されない
  4. 適性の低い案件は自動メール探索されない
  5. 適性の高い案件はメール探索へ進む
  6. 一覧・詳細・ランキング・Sales Copilot の API に campaign_url が含まれる
  7. 商品ページボタンが正しい外部 URL を開く（campaign_url がその案件のサイトを指す）
  8. 既存の Kickstarter/Indiegogo/Wadiz/Zeczec 処理が壊れていない
  9. Ulule が復活していない
 10. 手動 override 時に理由が記録される

実行（backend ディレクトリで）:
    python tests/test_campaign_url_gate.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# SessionLocal 束縛前に隔離した file sqlite を指定（dev DB を汚さない）
_DBFILE = os.path.join(tempfile.gettempdir(), "campaign_url_gate_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.models.contact_intelligence_job import CIJobType  # noqa: E402
from app.models.project import SALES_TARGET_SITES, Project, SourceSite  # noqa: E402
from app.schemas.project import ProjectOut  # noqa: E402
from app.services import campaign_url as cu  # noqa: E402
from app.services import contact_intelligence_service as ci  # noqa: E402
from app.services import contact_search_gate as gate  # noqa: E402
from app.services import product_context_service as pcs  # noqa: E402
from app.services import sales_copilot_service as cp  # noqa: E402
from app.services import workflow_service as wf  # noqa: E402

Base.metadata.create_all(engine)

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


# 各対応サイトの実在形式の商品ページ URL。
CAMPAIGN_URLS = {
    "kickstarter": "https://www.kickstarter.com/projects/acme/smart-mug",
    "indiegogo": "https://www.indiegogo.com/projects/acme-folding-chair",
    "wadiz": "https://www.wadiz.kr/web/campaign/detail/123456",
    "zeczec": "https://www.zeczec.com/projects/acme-lamp",
}

# 日本クラファン適性が高い（物販・小型・訴求点あり）案件の説明文。
_GOOD_DESC = (
    "A compact and portable stainless steel kitchen gadget with a minimal design. "
    "It solves the daily problem of keeping drinks warm, and is easy to carry."
)
# 適性が低い（非物販：ドキュメンタリー映画の寄付企画）案件の説明文。
_BAD_DESC = (
    "Support our documentary film and music album project. This is a donation-based "
    "charity fundraiser for a nonprofit association. No physical product is shipped."
)

_seq = 0


def mk(db, *, site: str, url: str | None, maker_url: str | None = None,
       desc: str = _GOOD_DESC, title: str = "Acme Smart Mug") -> Project:
    global _seq
    _seq += 1
    # source_url は一意制約があるため、ホストは保ったままパスだけ一意化する。
    unique_url = f"{url}-{_seq}" if url else None
    p = Project(
        title=f"{title} #{_seq}",
        source_site=site,
        source_url=unique_url,
        maker_url=maker_url,
        category="kitchen",
        description=desc,
        description_clean=desc,
        currency="USD",
        goal_amount=10000,
        raised_amount=45000,
        backers_count=900,
        maker_name="Acme Studio",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


# --------------------------------------------------------------------------- #
# 1. 各対応サイトの商品に campaign_url が保存される
# --------------------------------------------------------------------------- #
def test_campaign_url_per_site():
    print("test_campaign_url_per_site")
    db = SessionLocal()
    for site, url in CAMPAIGN_URLS.items():
        p = mk(db, site=site, url=url)
        check(f"{site}: campaign_url が取得できる", cu.campaign_url_of(p) == p.source_url)
        check(f"{site}: campaign_url_missing=False", p.campaign_url_missing is False)
        # 7. 商品ページボタンが正しい外部 URL を開く（そのサイトのドメインである）
        check(f"{site}: campaign_url がそのサイトのドメイン",
              cu.host_matches(p.campaign_url, site))
    db.close()


# --------------------------------------------------------------------------- #
# 2. campaign_url と official_site_url が混同されない
# --------------------------------------------------------------------------- #
def test_campaign_url_not_confused_with_official_site():
    print("test_campaign_url_not_confused_with_official_site")
    db = SessionLocal()
    ks = CAMPAIGN_URLS["kickstarter"]
    p = mk(db, site="kickstarter", url=ks, maker_url="https://acme-studio.com/")
    check("campaign_url は案件ページ", (p.campaign_url or "").startswith(ks))
    check("official_site_url はメーカー公式", p.official_site_url == "https://acme-studio.com/")
    check("両者は別物", p.campaign_url != p.official_site_url)

    # 公式サイトしか無い案件は campaign_url を公式サイトで代用しない
    only_official = mk(db, site="kickstarter", url=None, maker_url="https://acme-studio.com/")
    check("案件URLなし → campaign_url は None", only_official.campaign_url is None)
    check("案件URLなし → 公式で代用しない",
          only_official.campaign_url != only_official.official_site_url)
    check("欠落理由が残る", only_official.campaign_url_missing_reason == cu.REASON_NO_URL)

    # 公式サイト URL が source_url に入っていても campaign_url にはしない
    wrong = mk(db, site="kickstarter", url="https://acme-studio.com/product")
    check("サイト不一致 URL は campaign_url にしない", wrong.campaign_url is None)
    check("理由は not_campaign_domain",
          wrong.campaign_url_missing_reason == cu.REASON_NOT_CAMPAIGN_DOMAIN)
    db.close()


# --------------------------------------------------------------------------- #
# 3・4・5・10. 適性ゲート
# --------------------------------------------------------------------------- #
def test_gate_blocks_missing_campaign_url():
    print("test_gate_blocks_missing_campaign_url")
    db = SessionLocal()
    p = mk(db, site="kickstarter", url=None, maker_url="https://acme-studio.com/")
    result = gate.evaluate(db, p)
    check("campaign_url なしは不合格", result["eligible_for_contact_search"] is False)
    check("理由に商品ページURL未確認を含む",
          "商品ページURL未確認" in result["contact_search_gate_reason"])

    raised = False
    try:
        ci.create_job(db, p, CIJobType.full_contact_intelligence.value)
    except gate.GateBlocked:
        raised = True
    check("campaign_url なしは自動メール探索されない", raised)
    db.close()


def test_gate_blocks_low_fit():
    print("test_gate_blocks_low_fit")
    db = SessionLocal()
    p = mk(
        db,
        site="kickstarter",
        url=CAMPAIGN_URLS["kickstarter"],
        desc=_BAD_DESC,
        title="Our Documentary Film",
    )
    result = gate.evaluate(db, p)
    check("非物販企画は不合格", result["eligible_for_contact_search"] is False)
    check("判定は対象外 or 要確認",
          result["contact_search_gate_decision"] in (gate.GATE_NOT_ELIGIBLE,
                                                     gate.GATE_NEEDS_REVIEW))
    check("gate_checked_at が残る", p.gate_checked_at is not None)
    check("判定結果が projects に保存される", p.eligible_for_contact_search is False)

    raised = False
    try:
        ci.create_job(db, p, CIJobType.full_contact_intelligence.value)
    except gate.GateBlocked:
        raised = True
    check("適性が低い商品は自動メール探索されない", raised)
    db.close()


def test_gate_allows_high_fit():
    print("test_gate_allows_high_fit")
    db = SessionLocal()
    p = mk(db, site="kickstarter", url=CAMPAIGN_URLS["kickstarter"])
    result = gate.evaluate(db, p)
    check("適性の高い商品は合格", result["eligible_for_contact_search"] is True)
    check("スコアが閾値以上",
          (result["japan_crowdfunding_score"] or 0) >= gate.JAPAN_CF_SCORE_THRESHOLD)
    check("閾値は 1 か所（gate モジュール）で定義",
          result["japan_crowdfunding_threshold"] == gate.JAPAN_CF_SCORE_THRESHOLD)

    job, _cached = ci.create_job(db, p, CIJobType.full_contact_intelligence.value)
    check("適性の高い商品はメール探索へ進む", job is not None and job.status == "queued")
    check("通常実行は override 理由なし", job.gate_override_reason is None)
    db.close()


def test_manual_override_records_reason():
    print("test_manual_override_records_reason")
    db = SessionLocal()
    p = mk(db, site="kickstarter", url=CAMPAIGN_URLS["kickstarter"],
           desc=_BAD_DESC, title="Charity Documentary")
    job, _cached = ci.create_job(
        db, p, CIJobType.full_contact_intelligence.value,
        override_reason="担当者判断：物販商品であることを目視確認済み",
    )
    check("override で実行できる", job is not None)
    check("override 理由が記録される",
          job.gate_override_reason == "担当者判断：物販商品であることを目視確認済み")
    check("ログにも override が残る",
          any("手動実行" in (l.get("message") or "") for l in (job.logs_json or [])))
    db.close()


def test_gate_only_applies_to_contact_search():
    print("test_gate_only_applies_to_contact_search")
    db = SessionLocal()
    p = mk(db, site="kickstarter", url=None, desc=_BAD_DESC, title="Charity Film")
    # 日本販売状況チェックは探索ではないためゲート対象外（既存運用を壊さない）
    job, _cached = ci.create_job(db, p, CIJobType.japan_sales_check.value)
    check("探索以外のジョブはゲート対象外", job is not None)
    db.close()


# --------------------------------------------------------------------------- #
# 6. API レスポンスに campaign_url が含まれる
# --------------------------------------------------------------------------- #
def test_api_payloads_include_campaign_url():
    print("test_api_payloads_include_campaign_url")
    db = SessionLocal()
    p = mk(db, site="zeczec", url=CAMPAIGN_URLS["zeczec"],
           maker_url="https://acme-studio.com/")
    url = p.source_url

    # 一覧・詳細（ProjectOut）
    dto = ProjectOut.model_validate(p)
    check("ProjectOut に campaign_url", dto.campaign_url == url)
    check("ProjectOut に official_site_url", dto.official_site_url == "https://acme-studio.com/")
    check("ProjectOut の campaign_url_missing=False", dto.campaign_url_missing is False)

    # ランキング
    items = wf.ranking(db, limit=50, status_filter="all")
    mine = [i for i in items if i["project_id"] == p.id]
    check("ランキングの全件に campaign_url キーがある",
          bool(items) and all("campaign_url" in i for i in items))
    check("ランキングに campaign_url", bool(mine) and mine[0]["campaign_url"] == url)

    # 今日やること
    tasks = wf.today_tasks(db, per_group=50)
    flat = [t for group in tasks.values() if isinstance(group, list) for t in group]
    mine_t = [t for t in flat if t["project_id"] == p.id]
    check("今日やることの全件に campaign_url キーがある",
          bool(flat) and all("campaign_url" in t for t in flat))
    check("今日やることに campaign_url",
          bool(mine_t) and mine_t[0]["campaign_url"] == url)

    # Sales Copilot
    card = cp.project_copilot(db, p)
    check("Sales Copilot に campaign_url", card["campaign_url"] == url)
    check("Sales Copilot で公式サイトと分離",
          card["official_site_url"] == "https://acme-studio.com/")

    # 商品コンテキスト（メール探索画面の表示材料）
    ctx = pcs.build(db, p)
    check("商品コンテキストに campaign_url", ctx["campaign_url"] == url)
    check("商品コンテキストに日本語概要", bool(ctx["summary_ja"]))
    check("商品コンテキストに特徴3点まで", 1 <= len(ctx["key_features"]) <= 3)
    # 内部スコアは画面へ出さない（ゲート内部でのみ使う）。代わりに具体的な理由を返す。
    check("商品コンテキストに内部スコアを含めない",
          "japan_crowdfunding_score" not in ctx)
    check("商品コンテキストにメール探索の可否がある",
          "eligible_for_contact_search" in ctx)
    db.close()


# --------------------------------------------------------------------------- #
# 8・9. 既存サイトが壊れていない / Ulule が復活していない
# --------------------------------------------------------------------------- #
def test_supported_sites_intact():
    print("test_supported_sites_intact")
    values = [s.value for s in SALES_TARGET_SITES]
    for site in ("kickstarter", "indiegogo", "wadiz", "zeczec"):
        check(f"{site} は営業対象サイトのまま", site in values)
        check(f"{site} のドメイン定義がある", site in cu.PLATFORM_DOMAINS)

    check("Ulule は SourceSite に存在しない",
          not any(s.name == "ulule" for s in SourceSite))
    check("Ulule は営業対象サイトに存在しない", "ulule" not in values)
    check("Ulule のドメイン定義がない", "ulule" not in cu.PLATFORM_DOMAINS)

    from app.models.discovered_product import DiscoverySourcePlatform

    check("Ulule は発掘元プラットフォームにも存在しない",
          not any(pf.name == "ulule" for pf in DiscoverySourcePlatform))

    from app.scrapers.registry import get_scraper

    for site in (SourceSite.kickstarter, SourceSite.indiegogo,
                 SourceSite.wadiz, SourceSite.zeczec):
        scraper = get_scraper(site, limit=5)
        check(f"{site.value} のスクレイパーを取得できる", scraper is not None)
    db = SessionLocal()
    for site, url in CAMPAIGN_URLS.items():
        p = mk(db, site=site, url=url)
        g = gate.evaluate(db, p, persist=False)
        check(f"{site}: 通常の物販案件はゲートを通る",
              g["eligible_for_contact_search"] is True)
    db.close()


def main() -> int:
    test_campaign_url_per_site()
    test_campaign_url_not_confused_with_official_site()
    test_gate_blocks_missing_campaign_url()
    test_gate_blocks_low_fit()
    test_gate_allows_high_fit()
    test_manual_override_records_reason()
    test_gate_only_applies_to_contact_search()
    test_api_payloads_include_campaign_url()
    test_supported_sites_intact()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
