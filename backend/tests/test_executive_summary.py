"""AI Executive Summary のスコアリング検証（ネットワーク不要 / DB は in-memory SQLite）。

synthesize()（純粋関数）のスコア・営業対象・推奨アクション・推奨チャネル・理由/注意点を
検証し、build_summary() の end-to-end も SQLite で確認する。

pytest 非依存で単体実行できる。
実行（backend ディレクトリで）:
    python tests/test_executive_summary.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services import executive_summary_service as ess  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def sig(**over) -> dict:
    """既定（中立）シグナル。上書きでケースを作る。"""
    base = {
        "latest_score": None,
        "recommendation": None,
        "japan_checked": False,
        "no_japan_presence": False,
        "has_distributor": False,
        "sold_in_japan": False,
        "japan_stars": None,
        "contact_checked": False,
        "has_email": False,
        "has_form": False,
        "has_instagram": False,
        "has_linkedin": False,
        "has_facebook": False,
        "contactability_score": None,
        "contact_recommended_channel": None,
        "research_japan_fit": "",
        "similarity_top": None,
        "is_ulule": False,
        "is_sales_target_candidate": True,
        "ulule_europe_design": None,
        "ulule_sustainability": None,
        "ulule_gift": None,
        "ulule_jp_lifestyle": None,
        "ulule_sales_target_score": None,
        "sales_status": "not_started",
        "category": "キッチン",
    }
    base.update(over)
    return base


def test_high_value() -> None:
    print("高評価：連絡先あり・日本未販売・AI評価高")
    s = ess.synthesize(
        sig(
            latest_score=85,
            japan_checked=True,
            no_japan_presence=True,
            contact_checked=True,
            has_form=True,
            has_instagram=True,
            contactability_score=80,
            similarity_top=70,
            contact_recommended_channel="instagram",
        )
    )
    check("スコアが高い（>=60）", s["score"] >= 60)
    check("営業対象 = yes", s["sales_target"] == "yes")
    check("推奨アクション = 今すぐ営業", s["recommended_action"] == "今すぐ営業")
    check("推奨チャネル = instagram", s["recommended_channel"] == "instagram")
    check("日本販売状況 = 未販売の可能性が高い", s["japan_sales_status"] == "未販売の可能性が高い")
    check("代理店なし", s["japan_distributor_status"] == "代理店なし")
    check("理由が3〜5件", 3 <= len(s["reasons"]) <= 5)
    check("注意点が1〜3件", 1 <= len(s["cautions"]) <= 3)
    check("星は4〜5", s["stars"] >= 4)


def test_distributor_present() -> None:
    print("低評価：日本代理店あり")
    s = ess.synthesize(
        sig(
            latest_score=80,
            japan_checked=True,
            has_distributor=True,
            contact_checked=True,
            has_email=True,
        )
    )
    check("営業対象 = no", s["sales_target"] == "no")
    check("推奨アクション = 営業対象外の可能性", s["recommended_action"] == "営業対象外の可能性")
    check("代理店あり表示", s["japan_distributor_status"] == "代理店あり")


def test_sold_in_japan() -> None:
    print("低評価：日本販売済み")
    s_sold = ess.synthesize(
        sig(latest_score=70, japan_checked=True, sold_in_japan=True)
    )
    s_not = ess.synthesize(
        sig(latest_score=70, japan_checked=True, no_japan_presence=True)
    )
    check("販売済みは未販売よりスコアが低い", s_sold["score"] < s_not["score"])


def test_ulule_non_product() -> None:
    print("低評価：営業対象外Ulule（非商品）")
    s = ess.synthesize(
        sig(is_ulule=True, is_sales_target_candidate=False, category="ドキュメンタリー")
    )
    check("営業対象 = no", s["sales_target"] == "no")
    check("スコアが低い（<=35）", s["score"] <= 35)
    check("注意点に営業対象外を含む", any("営業対象外" in c for c in s["cautions"]))


def test_actions_branching() -> None:
    print("推奨アクションの分岐")
    s_nocheck = ess.synthesize(sig())
    check("チェック未実行 → 日本販売状況を確認", s_nocheck["recommended_action"] == "日本販売状況を確認")
    check("チェック未実行 → 営業対象 要確認 or no", s_nocheck["sales_target"] in ("要確認", "no"))

    s_nocontact = ess.synthesize(
        sig(japan_checked=True, no_japan_presence=True, contact_checked=True)
    )
    check("日本OK・連絡先なし → 連絡先探索が必要", s_nocontact["recommended_action"] == "連絡先探索が必要")

    s_engaged = ess.synthesize(
        sig(
            japan_checked=True,
            no_japan_presence=True,
            contact_checked=True,
            has_email=True,
            sales_status="awaiting_reply",
        )
    )
    check("営業着手済み → 後回し", s_engaged["recommended_action"] == "後回し")


def test_ulule_high_signals() -> None:
    print("Ulule の高シグナルは加点される")
    low = ess.synthesize(sig(is_ulule=True))
    high = ess.synthesize(
        sig(
            is_ulule=True,
            ulule_europe_design=85,
            ulule_sustainability=85,
            ulule_gift=80,
            ulule_jp_lifestyle=80,
        )
    )
    check("高Ululeスコアは加点", high["score"] > low["score"])
    check("理由にサステナブル/デザインを含む", any("サステナブル" in r or "デザイン" in r for r in high["reasons"]))


def test_build_summary_end_to_end() -> None:
    print("build_summary（SQLite end-to-end）")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    project = Project(
        title="Eco Bottle",
        source_site="kickstarter",
        maker_name="Acme",
        currency="USD",
        category="キッチン",
        latest_score=72,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    out = ess.build_summary(db, project)
    check("project_id を含む", out["project_id"] == project.id)
    check("score は 0〜100", 0 <= out["score"] <= 100)
    check("stars は 1〜5", 1 <= out["stars"] <= 5)
    check("sales_target は許可値", out["sales_target"] in ("yes", "no", "要確認"))
    check("推奨チャネルは許可セット", out["recommended_channel"] in ess.ALLOWED_CHANNELS)
    db.close()


def test_lightweight_similarity() -> None:
    print("軽量類似シグナル（同カテゴリ EXISTS・全件走査しない）")
    from app.models.japanese_success import JapaneseSuccessProject

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    project = Project(
        title="Eco Bottle",
        source_site="kickstarter",
        currency="USD",
        category="キッチン",
    )
    db.add(project)
    db.add(
        JapaneseSuccessProject(
            title="まほうびん",
            platform="makuake",
            source_url="https://www.makuake.com/project/x1",
            category="キッチン",
            currency="JPY",
        )
    )
    db.commit()
    db.refresh(project)
    check("同カテゴリ成功事例を EXISTS で検出", ess._has_similar_category(db, project) is True)
    sig = ess._gather_signals(db, project)
    check("類似シグナルが立つ", sig["similarity_top"] == 70)

    # カテゴリ不一致なら類似なし
    project.category = "ガジェット"
    db.commit()
    check("不一致カテゴリは類似なし", ess._has_similar_category(db, project) is False)
    db.close()


def main() -> int:
    test_high_value()
    test_distributor_present()
    test_sold_in_japan()
    test_ulule_non_product()
    test_actions_branching()
    test_ulule_high_signals()
    test_build_summary_end_to_end()
    test_lightweight_similarity()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
