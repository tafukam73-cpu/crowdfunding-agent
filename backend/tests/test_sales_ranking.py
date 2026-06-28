"""AI 営業優先ランキングの検証（ネットワーク不要 / DB は in-memory SQLite）。

workflow_service.ranking() が Executive Summary を統合してスコア順に並べ、
フィルタ（営業対象候補のみ / 未営業のみ / 連絡先ありのみ / 日本未販売のみ /
Ulule のみ / サイト切替）が効くことを確認する。

pytest 非依存で単体実行できる。
実行（backend ディレクトリで）:
    python tests/test_sales_ranking.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.ai.japan_sales_checker import CHANNEL_KEYS, build_channels  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.japan_sales_check import JapanSalesCheck  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services import workflow_service  # noqa: E402

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


def _split_part(s, delim, n):
    """Postgres の split_part 相当（本番は Postgres、テストの SQLite 用に登録する）。"""
    if s is None:
        return ""
    parts = s.split(delim)
    return parts[n - 1] if 1 <= n <= len(parts) else ""


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")

    # _non_candidate_condition() が使う split_part を SQLite に登録（本番は Postgres）
    @event.listens_for(engine, "connect")
    def _register(dbapi_conn, _rec):  # noqa: ANN001
        dbapi_conn.create_function("split_part", 3, _split_part)

    Base.metadata.create_all(engine)
    return Session(engine)


def _add_unsold_check(db: Session, project_id: int) -> None:
    statuses = {k: "not_found" for k in CHANNEL_KEYS}
    db.add(
        JapanSalesCheck(
            project_id=project_id,
            status="completed",
            sales_value_stars=5,
            channels=build_channels("p", "m", statuses),
            ai_comment="未販売",
            summary="未販売の可能性が高い",
        )
    )


def _add_contact(db: Session, project_id: int) -> None:
    db.add(
        ContactDiscovery(
            project_id=project_id,
            status="completed",
            primary_contact_form_url="https://example.com/contact",
            instagram_url="https://instagram.com/brand",
            contactability_score=80,
            recommended_channel="instagram",
        )
    )


def _seed() -> Session:
    db = _session()
    # A: 高評価（未販売 + 連絡先あり + 高AI評価 + 未営業）
    a = Project(title="Eco Kitchen Tool", source_site="ulule", currency="EUR",
                category="キッチン", description="sustainable kitchen reusable design",
                latest_score=90, sales_status="not_started")
    # B: 契約済み（営業価値はあるが優先度は下がる）
    b = Project(title="Done Deal", source_site="kickstarter", currency="USD",
                category="ガジェット", description="gadget", latest_score=88,
                sales_status="won")
    # C: 営業対象外 Ulule（非商品：ドキュメンタリー）
    c = Project(title="Doc Film", source_site="ulule", currency="EUR",
                category="映画", description="a documentary film about music festival",
                latest_score=70, sales_status="not_started")
    # D: 連絡先なし・チェックなし（中位）
    d = Project(title="Plain Gadget", source_site="indiegogo", currency="USD",
                category="ガジェット", description="gadget device", latest_score=60,
                sales_status="not_started")
    db.add_all([a, b, c, d])
    db.commit()
    for p in (a, b, c, d):
        db.refresh(p)
    _add_unsold_check(db, a.id)
    _add_contact(db, a.id)
    db.commit()
    return db


def test_default_ranking() -> None:
    print("既定ランキング（スコア降順・営業対象候補のみ）")
    db = _seed()
    items = workflow_service.ranking(db, limit=20)
    titles = [it["title"] for it in items]
    check("営業対象外 Ulule（Doc Film）は除外", "Doc Film" not in titles)
    check("先頭は高評価の Eco Kitchen Tool", items and items[0]["title"] == "Eco Kitchen Tool")
    check("rank が 1 から振られる", items[0]["rank"] == 1)
    # スコア降順
    scores = [it["score"] for it in items]
    check("スコア降順", scores == sorted(scores, reverse=True))
    # 契約済み B は未営業 A より下
    pos = {it["title"]: it["rank"] for it in items}
    check("契約済みは高評価でも下位", pos.get("Done Deal", 99) > pos["Eco Kitchen Tool"])
    # A の表示項目
    top = items[0]
    check("営業対象 yes", top["sales_target"] == "yes")
    check("推奨チャネル instagram", top["recommended_channel"] == "instagram")
    check("日本未販売テキスト", top["japan_sales_status"] == "未販売の可能性が高い")
    check("理由が3件以上", len(top["reasons"]) >= 3)
    db.close()


def test_filters() -> None:
    print("フィルタ")
    db = _seed()

    not_started = workflow_service.ranking(db, not_started_only=True)
    check("未営業のみ → 契約済みを除外", all(it["title"] != "Done Deal" for it in not_started))

    contact_only = workflow_service.ranking(db, contact_only=True)
    check("連絡先ありのみ → Eco Kitchen Tool のみ",
          [it["title"] for it in contact_only] == ["Eco Kitchen Tool"])

    unsold = workflow_service.ranking(db, unsold_only=True)
    check("日本未販売のみ → Eco Kitchen Tool のみ",
          [it["title"] for it in unsold] == ["Eco Kitchen Tool"])

    ulule = workflow_service.ranking(db, candidates_only=False, ulule_only=True)
    titles = [it["title"] for it in ulule]
    check("Ulule のみ → Ulule 案件だけ", set(titles) <= {"Eco Kitchen Tool", "Doc Film"})
    check("Ulule のみ（対象外含む）に Doc Film が出る", "Doc Film" in titles)

    site_ks = workflow_service.ranking(db, site="kickstarter", candidates_only=False)
    check("サイト=kickstarter → Done Deal のみ",
          [it["title"] for it in site_ks] == ["Done Deal"])
    db.close()


def test_sorts() -> None:
    print("並び順")
    db = _seed()
    by_new = workflow_service.ranking(db, sort="created_at", candidates_only=False)
    check("新着順で全件返る（4件）", len(by_new) == 4)
    by_contact = workflow_service.ranking(db, sort="contact")
    check("連絡先あり優先 → 先頭は Eco Kitchen Tool",
          by_contact and by_contact[0]["title"] == "Eco Kitchen Tool")
    db.close()


def main() -> int:
    test_default_ranking()
    test_filters()
    test_sorts()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
