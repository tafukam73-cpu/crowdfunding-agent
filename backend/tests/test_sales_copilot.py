"""営業 AI コパイロットの判断ロジック・ダッシュボード分類のオフライン検証。

要件のテスト観点を pytest 非依存（`python tests/test_sales_copilot.py`）で検証する:
  - メールなし案件は連絡先探索（needs_contact）に分類
  - 高スコア・未営業・連絡先あり案件は優先営業（sell_now）に分類
  - 返信待ち7日以上はフォロー（needs_followup）に分類
  - 公式サイトなしはデータ不足（data_insufficient）に分類
  - 見送り(rejected)/成約(won)は営業候補から除外（closed）
  - 判断理由（primary_reason / reasons）が必ず返る

さらに DB を使ったダッシュボードのバケット分類（優先営業 / 連絡先探索 / データ不足 /
見送り除外・理由付き）を検証する。

実行（backend ディレクトリで）:
    python tests/test_sales_copilot.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "sales_copilot_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.project import Project, SalesStatus  # noqa: E402
from app.services import sales_copilot_service as cp  # noqa: E402

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


# ---------------------------------------------------------------------------
#  1) classify() 純粋関数の分類ルール
# ---------------------------------------------------------------------------
def _sig(**over) -> dict:
    """既定は「未営業・評価あり・公式サイトあり・連絡先なし」の案件シグナル。"""
    base = {
        "sales_status": SalesStatus.not_started.value,
        "latest_score": 50,
        "is_sales_target_candidate": True,
        "has_email": False,
        "has_form": False,
        "has_instagram": False,
        "has_linkedin": False,
        "has_facebook": False,
        "has_distributor": False,
        "sold_in_japan": False,
        "contact_checked": True,
        "has_official_site": True,
        "research_done": False,
        "email_done": False,
        "days_since_last_outreach": None,
    }
    base.update(over)
    return base


def test_classify_rules():
    print("test_classify_rules")

    # メールなし（連絡先なし）＋公式サイトあり → 連絡先探索
    r = cp.classify(_sig(latest_score=70, has_official_site=True))
    check("メールなしは needs_contact", r["decision"] == "needs_contact")

    # 高スコア・未営業・連絡先あり → 優先営業
    r = cp.classify(_sig(latest_score=75, has_email=True))
    check("高スコア＋連絡先ありは sell_now", r["decision"] == "sell_now")

    # 返信待ち7日以上 → フォロー
    r = cp.classify(
        _sig(sales_status=SalesStatus.awaiting_reply.value, days_since_last_outreach=7)
    )
    check("返信待ち7日は needs_followup", r["decision"] == "needs_followup")

    # 公式サイトなし（連絡先なし）→ データ不足
    r = cp.classify(_sig(latest_score=60, has_official_site=False))
    check("公式サイトなしは data_insufficient", r["decision"] == "data_insufficient")

    # 未評価（連絡先なし）→ データ不足
    r = cp.classify(_sig(latest_score=None, has_official_site=True))
    check("未評価は data_insufficient", r["decision"] == "data_insufficient")

    # 成約 / 見送りは closed（営業候補から除外）
    check(
        "成約は closed",
        cp.classify(_sig(sales_status=SalesStatus.won.value))["decision"] == "closed",
    )
    check(
        "見送りは closed",
        cp.classify(_sig(sales_status=SalesStatus.rejected.value))["decision"]
        == "closed",
    )

    # 営業対象外（非物販）→ 見送り候補
    r = cp.classify(_sig(is_sales_target_candidate=False))
    check("非物販は drop", r["decision"] == "drop")
    # 日本に代理店 / 販売済み → 見送り候補
    check(
        "代理店ありは drop",
        cp.classify(_sig(has_distributor=True))["decision"] == "drop",
    )
    check(
        "日本販売済みは drop",
        cp.classify(_sig(sold_in_japan=True))["decision"] == "drop",
    )

    # 連絡先あり・低スコア・メール未生成 → メール生成が必要
    r = cp.classify(_sig(latest_score=50, has_email=True, email_done=False))
    check("連絡先あり低スコアは needs_email", r["decision"] == "needs_email")

    # 連絡先あり・メール済み・リサーチ未 → 企業リサーチが必要
    r = cp.classify(
        _sig(latest_score=50, has_email=True, email_done=True, research_done=False)
    )
    check("メール済み・リサーチ未は needs_research", r["decision"] == "needs_research")

    # 営業済み・返信待ち期間内（3日未満）→ 待機
    r = cp.classify(
        _sig(sales_status=SalesStatus.contacted.value, days_since_last_outreach=1)
    )
    check("営業済み3日未満は waiting", r["decision"] == "waiting")

    # 返信あり / 商談中 → 商談対応
    check(
        "返信ありは needs_negotiation",
        cp.classify(_sig(sales_status=SalesStatus.replied.value))["decision"]
        == "needs_negotiation",
    )
    check(
        "商談中は needs_negotiation",
        cp.classify(_sig(sales_status=SalesStatus.negotiating.value))["decision"]
        == "needs_negotiation",
    )

    # 判断理由は必ず返る（全ケースで primary_reason が非空）
    all_ok = True
    for over in (
        {},
        {"has_email": True, "latest_score": 90},
        {"sales_status": SalesStatus.won.value},
        {"is_sales_target_candidate": False},
        {"has_official_site": False},
        {"sales_status": SalesStatus.awaiting_reply.value, "days_since_last_outreach": 9},
    ):
        r = cp.classify(_sig(**over))
        if not r.get("primary_reason"):
            all_ok = False
    check("すべての判断で理由が返る", all_ok)


# ---------------------------------------------------------------------------
#  2) DB を使ったダッシュボードのバケット分類
# ---------------------------------------------------------------------------
_seq = 0


def _mk(db, *, status, score, title):
    global _seq
    _seq += 1
    p = Project(
        title=title,
        source_site="kickstarter",
        source_url=f"https://kck.st/cp-{_seq}",
        sales_status=status,
        latest_score=score,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _cd(db, project_id, *, email=None, official=None):
    row = ContactDiscovery(
        project_id=project_id,
        primary_email=email,
        official_site_url=official,
    )
    db.add(row)
    db.commit()


def test_dashboard_buckets():
    print("test_dashboard_buckets")
    db = SessionLocal()
    try:
        p_sell = _mk(db, status=SalesStatus.not_started.value, score=80, title="Sell Now Co")
        _cd(db, p_sell.id, email="hello@brandone.com", official="https://brandone.com")

        p_contact = _mk(
            db, status=SalesStatus.not_started.value, score=70, title="Need Contact Co"
        )
        _cd(db, p_contact.id, official="https://brandtwo.com")  # 公式のみ・メールなし

        p_data = _mk(
            db, status=SalesStatus.not_started.value, score=55, title="No Site Co"
        )  # ContactDiscovery なし → 公式サイト未確認

        p_won = _mk(db, status=SalesStatus.won.value, score=90, title="Won Co")
        _cd(db, p_won.id, email="x@brandwon.com", official="https://brandwon.com")

        d = cp.copilot_dashboard(db, per_bucket=5)

        def ids(bucket):
            return {c["project_id"] for c in d[bucket]}

        check("優先営業に sell_now 案件", p_sell.id in ids("priority_sales"))
        check("連絡先探索に needs_contact 案件", p_contact.id in ids("needs_contact"))
        check("データ不足に公式サイトなし案件", p_data.id in ids("data_insufficient"))

        actionable_ids = (
            ids("priority_sales")
            | ids("needs_contact")
            | ids("needs_email")
            | ids("followup")
            | ids("data_insufficient")
        )
        check("成約案件は営業バケットから除外", p_won.id not in actionable_ids)
        check("closed が集計に含まれる", d["counts"].get("closed", 0) >= 1)

        # すべてのカードに理由が 1 件以上ある
        all_cards = []
        for b in (
            "priority_sales",
            "needs_contact",
            "needs_email",
            "followup",
            "drop_candidates",
            "data_insufficient",
        ):
            all_cards += d[b]
        if d["top_action"]:
            all_cards.append(d["top_action"])
        check(
            "全カードに判断理由がある",
            all(len(c["reasons"]) >= 1 for c in all_cards),
        )
        check("AIコメントが返る", bool(d["ai_comment"]))
        check("走査件数が一致", d["scanned"] == 4)

        # サマリーに要件 1 の主要項目が含まれる
        card = cp.project_copilot(db, p_sell)
        s = card["summary"]
        need = {
            "product",
            "company",
            "japan_market_fit",
            "funding",
            "contact_status",
            "sales_status",
            "last_action",
            "next_action",
            "risks",
            "recommendation",
        }
        check("サマリーに必須項目が揃う", need <= set(s.keys()))
        check("p_sell は sell_now と判断", card["decision"] == "sell_now")
    finally:
        db.close()


if __name__ == "__main__":
    test_classify_rules()
    test_dashboard_buckets()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
