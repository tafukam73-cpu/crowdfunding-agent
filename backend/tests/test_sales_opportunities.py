"""営業案件管理（Contact Intelligence v5）のオフライン検証（ネットワーク不要）。

Contact Discovery からの冪等な営業案件作成・ステータス/次アクション更新・スコア順
一覧・フィルターを sqlite で検証する。pytest 非依存で単体実行できる。

実行（backend ディレクトリで）:
    python tests/test_sales_opportunities.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "sales_opps_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services import sales_opportunity_service as svc  # noqa: E402

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


def _mk_discovery(db, *, title, maker, score, email=None, channel="email",
                  official="https://brandco.com") -> ContactDiscovery:
    p = Project(title=title, maker_name=maker, source_site="kickstarter")
    db.add(p)
    db.commit()
    db.refresh(p)
    cd = ContactDiscovery(
        project_id=p.id, status="completed",
        official_site_url=official,
        contactability_score=score,
        recommended_channel=channel,
        recommended_action="問い合わせフォームから連絡する",
        primary_email=email,
        instagram_url="https://www.instagram.com/brandco/",
        linkedin_url="https://www.linkedin.com/company/brandco/",
    )
    db.add(cd)
    db.commit()
    db.refresh(cd)
    return cd


def test_create_from_discovery():
    print("test_create_from_discovery")
    db = SessionLocal()
    cd = _mk_discovery(db, title="Cool Lamp", maker="BrandCo", score=85,
                       email="hello@brandco.com")
    opp, created = svc.create_from_contact_discovery(db, cd.id)
    check("作成できる", created is True and opp.id is not None)
    check("会社名を引き継ぐ", opp.company_name == "BrandCo")
    check("商品名を引き継ぐ", opp.product_name == "Cool Lamp")
    check("公式サイトを引き継ぐ", opp.website_url == "https://brandco.com")
    check("営業スコアを引き継ぐ", opp.sales_score == 85)
    check("優先度 high（score>=70）", opp.sales_priority == "high")
    check("推奨チャネルを引き継ぐ", opp.recommended_channel == "email")
    check("主要メールを引き継ぐ", opp.primary_email == "hello@brandco.com")
    check("主要SNS（linkedin優先）",
          opp.primary_social_url == "https://www.linkedin.com/company/brandco/")
    check("次アクションを引き継ぐ", bool(opp.next_action))
    check("初期ステータス not_started", opp.status == "not_started")
    db.close()


def test_idempotent():
    print("test_idempotent")
    db = SessionLocal()
    cd = _mk_discovery(db, title="Idem", maker="IdemCo", score=50)
    opp1, c1 = svc.create_from_contact_discovery(db, cd.id)
    opp2, c2 = svc.create_from_contact_discovery(db, cd.id)
    check("1回目は作成", c1 is True)
    check("2回目は再利用（重複作成しない）", c2 is False and opp2.id == opp1.id)
    # 総数が増えていない
    all_for_cd = [
        o for o in svc.list_opportunities(db)
        if o.contact_discovery_id == cd.id
    ]
    check("同一 cd の案件は1件のみ", len(all_for_cd) == 1)
    check("priority medium（40<=score<70）", opp1.sales_priority == "medium")
    db.close()


def test_missing_discovery():
    print("test_missing_discovery")
    db = SessionLocal()
    try:
        svc.create_from_contact_discovery(db, 999999)
        check("存在しない cd で ValueError", False)
    except ValueError:
        check("存在しない cd で ValueError", True)
    db.close()


def test_status_update():
    print("test_status_update")
    db = SessionLocal()
    cd = _mk_discovery(db, title="Upd", maker="UpdCo", score=30)
    opp, _ = svc.create_from_contact_discovery(db, cd.id)
    check("priority low（score<40）", opp.sales_priority == "low")
    updated = svc.update(db, opp.id, status="contacted")
    check("ステータス更新できる", updated.status == "contacted")
    # 不正な status
    try:
        svc.update(db, opp.id, status="bogus")
        check("不正 status で ValueError", False)
    except ValueError:
        check("不正 status で ValueError", True)
    db.close()


def test_next_action_and_notes_update():
    print("test_next_action_and_notes_update")
    db = SessionLocal()
    cd = _mk_discovery(db, title="Act", maker="ActCo", score=60)
    opp, _ = svc.create_from_contact_discovery(db, cd.id)
    due = date(2026, 8, 1)
    updated = svc.update(
        db, opp.id, next_action="サンプル送付", next_action_due_date=due,
        notes="担当は田中さん",
    )
    check("次アクション更新", updated.next_action == "サンプル送付")
    check("期限更新", updated.next_action_due_date == due)
    check("メモ更新", updated.notes == "担当は田中さん")
    # 部分更新（notes 未指定なら据え置き）
    updated2 = svc.update(db, opp.id, status="waiting_reply")
    check("部分更新でメモ据え置き", updated2.notes == "担当は田中さん")
    check("部分更新でステータス変更", updated2.status == "waiting_reply")
    db.close()


def test_list_sort_and_filter():
    print("test_list_sort_and_filter")
    db = SessionLocal()
    # 3 件（スコア 90 / 55 / 20）
    cd_hi = _mk_discovery(db, title="Hi", maker="Hi", score=90)
    cd_mid = _mk_discovery(db, title="Mid", maker="Mid", score=55)
    cd_lo = _mk_discovery(db, title="Lo", maker="Lo", score=20)
    o_hi, _ = svc.create_from_contact_discovery(db, cd_hi.id)
    o_mid, _ = svc.create_from_contact_discovery(db, cd_mid.id)
    o_lo, _ = svc.create_from_contact_discovery(db, cd_lo.id)

    ranked = svc.list_opportunities(db, sort="score")
    scores = [o.sales_score for o in ranked]
    check("スコア降順に並ぶ", scores == sorted(scores, reverse=True))
    check("最上位は score 90", ranked[0].sales_score == 90)

    # フィルター：status
    svc.update(db, o_hi.id, status="meeting")
    only_meeting = svc.list_opportunities(db, status="meeting")
    check("status フィルター", all(o.status == "meeting" for o in only_meeting)
          and any(o.id == o_hi.id for o in only_meeting))

    # フィルター：priority
    only_high = svc.list_opportunities(db, sales_priority="high")
    check("priority フィルター", all(o.sales_priority == "high" for o in only_high))

    # フィルター：min_score
    ge50 = svc.list_opportunities(db, min_score=50)
    check("min_score フィルター", all((o.sales_score or 0) >= 50 for o in ge50)
          and all(o.id != o_lo.id for o in ge50))

    # 期限順（未設定は末尾）
    svc.update(db, o_lo.id, next_action_due_date=date(2026, 1, 1))
    svc.update(db, o_mid.id, next_action_due_date=date(2026, 12, 1))
    by_due = svc.list_opportunities(db, sort="due_date")
    with_due = [o for o in by_due if o.next_action_due_date is not None]
    check("期限順：最初は最も近い期限",
          with_due[0].next_action_due_date == date(2026, 1, 1))
    # 未設定の案件は末尾側にある（最初ではない）
    check("期限未設定は末尾へ", by_due[0].next_action_due_date is not None)
    db.close()


def test_get_and_priority_helper():
    print("test_get_and_priority_helper")
    db = SessionLocal()
    cd = _mk_discovery(db, title="G", maker="GCo", score=75)
    opp, _ = svc.create_from_contact_discovery(db, cd.id)
    got = svc.get(db, opp.id)
    check("id で取得できる", got is not None and got.id == opp.id)
    check("存在しない id は None", svc.get(db, 987654) is None)
    check("priority_from_score(70)=high", svc.priority_from_score(70) == "high")
    check("priority_from_score(40)=medium", svc.priority_from_score(40) == "medium")
    check("priority_from_score(0)=low", svc.priority_from_score(0) == "low")
    db.close()


def main():
    test_create_from_discovery()
    test_idempotent()
    test_missing_discovery()
    test_status_update()
    test_next_action_and_notes_update()
    test_list_sort_and_filter()
    test_get_and_priority_helper()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
