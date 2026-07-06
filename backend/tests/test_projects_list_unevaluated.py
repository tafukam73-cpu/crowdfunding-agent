"""海外営業対象案件一覧が未評価案件も含めて返すことのオフライン検証（ネットワーク不要）。

「新規収集したが AI 未評価」の案件（特に Kickstarter）が /projects 一覧から
除外されていないことを保証する。candidates_only は非物販の Ulule 案件のみを除外し、
未評価の Kickstarter/Indiegogo を落とさないことも確認する。

実行（backend ディレクトリで）:
    python tests/test_projects_list_unevaluated.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "projects_list_unevaluated_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import event  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.project import Project  # noqa: E402
from app.services import project_service as ps  # noqa: E402


# PostgreSQL の split_part を sqlite にも用意（candidates_only の判定式が使うため）。
@event.listens_for(engine, "connect")
def _register_split_part(dbapi_conn, _rec):
    def split_part(text, delim, n):
        if text is None:
            return ""
        parts = str(text).split(delim)
        return parts[n - 1] if 1 <= n <= len(parts) else ""

    dbapi_conn.create_function("split_part", 3, split_part)


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


def _mk(db, *, site: str, title: str, score=None) -> Project:
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


def test_unevaluated_projects_are_listed():
    print("test_unevaluated_projects_are_listed")
    db = SessionLocal()
    scored_ks = _mk(db, site="kickstarter", title="Scored KS gadget", score=80)
    new_ks = _mk(db, site="kickstarter", title="Brand New KS gadget")  # 未評価
    new_igg = _mk(db, site="indiegogo", title="New IGG bottle")        # 未評価
    # 非物販の Ulule（charity・商品語なし）→ 営業対象外（candidates_only で除外される）
    non_cand_ulule = _mk(db, site="ulule", title="charity fundraiser association")

    items, total = ps.list_projects(db, candidates_only=True, page_size=100)
    ids = {p.id for p in items}

    check("スコア済み Kickstarter は表示される", scored_ks.id in ids)
    check("未評価の新規 Kickstarter も表示される", new_ks.id in ids)
    check("未評価の新規 Indiegogo も表示される", new_igg.id in ids)
    check("非物販の Ulule は candidates_only で除外", non_cand_ulule.id not in ids)
    check("total は未評価も数える（=候補3件）", total == 3)

    scored = sum(1 for p in items if p.latest_score is not None)
    check("未評価だけに絞られていない（scored < total）",
          scored == 1 and len(items) == 3)
    db.close()


def test_candidates_only_off_shows_everything():
    print("test_candidates_only_off_shows_everything")
    db = SessionLocal()
    _all_items, total_all = ps.list_projects(db, candidates_only=False, page_size=100)
    _cand_items, total_cand = ps.list_projects(db, candidates_only=True, page_size=100)
    check("営業対象外を含めると件数が増える（非物販Ululeを含む）",
          total_all == total_cand + 1)
    db.close()


def test_no_default_score_filter():
    print("test_no_default_score_filter")
    db = SessionLocal()
    # 既定（candidates_only=False）でスコアの有無に関わらず返す
    items, _ = ps.list_projects(db, candidates_only=False, page_size=100)
    has_unscored = any(p.latest_score is None for p in items)
    has_scored = any(p.latest_score is not None for p in items)
    check("既定で未評価案件も含まれる", has_unscored)
    check("既定でスコア済み案件も含まれる", has_scored)
    db.close()


def main():
    test_unevaluated_projects_are_listed()
    test_candidates_only_off_shows_everything()
    test_no_default_score_filter()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
