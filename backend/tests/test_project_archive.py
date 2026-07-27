"""営業対象外（ソフトデリート）のオフライン検証（ネットワーク不要・SQLite）。

営業価値の低い案件を「営業対象外」にすると、通常の一覧・ランキング・Today Tasks から
除外され、「除外済み案件」一覧では確認・復元できることを保証する。関連データは削除せず、
理由（archive_reason）は将来の分析用に保存されることも確認する。

実行（backend ディレクトリで）:
    python tests/test_project_archive.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "project_archive_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.models.project import Project  # noqa: E402
from app.services import evaluation_service as es  # noqa: E402
from app.services import project_service as ps  # noqa: E402
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


_seq = 0


def _mk(db, *, title: str, site: str = "kickstarter", score=None) -> Project:
    global _seq
    _seq += 1
    p = Project(
        title=title,
        source_site=site,
        source_url=f"https://ex.com/{site}-{_seq}",
        latest_score=score,
        latest_recommendation=("high" if score is not None else None),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_archive_excludes_from_list_and_restores():
    print("test_archive_excludes_from_list_and_restores")
    db = SessionLocal()
    keep = _mk(db, title="Keep gadget", score=80)
    drop = _mk(db, title="Low value gadget", score=10)

    # 営業対象外にする（理由つき）
    ps.archive_project(db, drop, reason="日本市場に不向き")
    check("archived_at がセットされる", drop.archived_at is not None)
    check("is_archived が True", drop.is_archived is True)
    check("archive_reason が保存される", drop.archive_reason == "日本市場に不向き")

    # 通常一覧（archived=False）からは除外
    items, total = ps.list_projects(db, page_size=100)
    ids = {p.id for p in items}
    check("通常一覧に対象外案件は出ない", drop.id not in ids)
    check("通常一覧に通常案件は出る", keep.id in ids)
    check("total は対象外を数えない（=1）", total == 1)

    # 除外済み一覧（archived=True）には出る
    arch_items, arch_total = ps.list_projects(db, archived=True, page_size=100)
    arch_ids = {p.id for p in arch_items}
    check("除外済み一覧に対象外案件が出る", drop.id in arch_ids)
    check("除外済み一覧に通常案件は出ない", keep.id not in arch_ids)
    check("除外済み total は対象外のみ（=1）", arch_total == 1)

    # 復元
    ps.unarchive_project(db, drop)
    check("復元で archived_at が None", drop.archived_at is None)
    check("復元で archive_reason も消える", drop.archive_reason is None)
    items2, total2 = ps.list_projects(db, page_size=100)
    check("復元後は通常一覧に戻る", drop.id in {p.id for p in items2} and total2 == 2)
    db.close()


def test_bulk_archive_and_unarchive():
    print("test_bulk_archive_and_unarchive")
    db = SessionLocal()
    a = _mk(db, title="Bulk A", score=30)
    b = _mk(db, title="Bulk B", score=40)
    c = _mk(db, title="Bulk C", score=50)

    n = ps.archive_projects(db, [a.id, b.id, 999999], reason="その他")
    check("存在する2件のみ更新（999999 は無視）", n == 2)
    for p in (a, b):
        db.refresh(p)
    check("A/B は理由つきで対象外", a.is_archived and b.archive_reason == "その他")

    items, _ = ps.list_projects(db, page_size=100)
    ids = {p.id for p in items}
    check("一括対象外は通常一覧から消える", a.id not in ids and b.id not in ids)
    check("対象外にしていない C は残る", c.id in ids)

    m = ps.unarchive_projects(db, [a.id, b.id])
    check("一括復元は2件", m == 2)
    db.refresh(a)
    check("一括復元で戻る", a.archived_at is None)
    db.close()


def test_archive_excluded_from_ranking_and_today_tasks():
    print("test_archive_excluded_from_ranking_and_today_tasks")
    db = SessionLocal()
    live = _mk(db, title="Live ranking gadget", score=90)
    hidden = _mk(db, title="Hidden ranking gadget", score=95)
    ps.archive_project(db, hidden, reason="商品ではない")

    ranked_ids = {r["project_id"] for r in wf.ranking(db, limit=50)}
    check("ランキングに対象外は出ない", hidden.id not in ranked_ids)
    check("ランキングに通常案件は出る", live.id in ranked_ids)

    tasks = wf.today_tasks(db)
    task_ids = set()
    for group in tasks.values():
        if isinstance(group, list):
            task_ids.update(item.get("project_id") for item in group)
    check("Today Tasks に対象外は出ない", hidden.id not in task_ids)
    db.close()


def test_archive_excluded_from_unevaluated_count():
    print("test_archive_excluded_from_unevaluated_count")
    db = SessionLocal()
    _mk(db, title="Unevaluated live")          # 未評価・通常
    hidden = _mk(db, title="Unevaluated hidden")  # 未評価・対象外
    before = es.count_unevaluated(db)
    ps.archive_project(db, hidden, reason="メーカー連絡先なし")
    after = es.count_unevaluated(db)
    check("対象外にすると未評価件数が1減る", after == before - 1)
    db.close()


def main():
    test_archive_excludes_from_list_and_restores()
    test_bulk_archive_and_unarchive()
    test_archive_excluded_from_ranking_and_today_tasks()
    test_archive_excluded_from_unevaluated_count()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
