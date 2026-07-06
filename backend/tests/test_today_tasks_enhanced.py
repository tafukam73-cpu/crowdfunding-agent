"""営業アシスタント「今日やること」の分類・フォロー判定のオフライン検証（DB のみ）。

未営業/営業済み/返信待ち/商談中/成約/見送りを作り、today_tasks が
to_contact / followup / replied / negotiating / idle に正しく振り分けること、
最終営業日からの経過日数でフォロー優先度（normal/high/final）が決まること、
成約/見送りは除外されることを検証する。

実行（backend ディレクトリで）:
    python tests/test_today_tasks_enhanced.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "today_tasks_enhanced_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.crm import Maker, SalesActivity  # noqa: E402
from app.models.project import Project, SalesStatus  # noqa: E402
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


def _mk(db, *, status: str, score=None, title=None) -> Project:
    global _seq
    _seq += 1
    p = Project(
        title=title or f"proj-{status}-{_seq}",
        source_site="kickstarter",
        source_url=f"https://kck.st/tt-{_seq}",
        sales_status=status,
        latest_score=score,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _outreach(db, maker_id: int, project_id: int, *, days_ago: int) -> None:
    """最終営業日を days_ago 日前に設定する SalesActivity を作る。"""
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    db.add(
        SalesActivity(
            maker_id=maker_id,
            project_id=project_id,
            kind="email",
            summary="test outreach",
            occurred_at=ts,
            created_at=ts,
        )
    )
    db.commit()


def _ids(items) -> set[int]:
    return {it["project_id"] for it in items}


def _find(items, pid):
    return next((it for it in items if it["project_id"] == pid), None)


def test_to_contact_and_exclusions():
    print("test_to_contact_and_exclusions")
    db = SessionLocal()
    hi = _mk(db, status=SalesStatus.not_started.value, score=90)
    lo = _mk(db, status=SalesStatus.ready.value, score=40)
    contacted = _mk(db, status=SalesStatus.contacted.value, score=95)
    won = _mk(db, status=SalesStatus.won.value, score=99)
    rejected = _mk(db, status=SalesStatus.rejected.value, score=99)

    t = wf.today_tasks(db, per_group=10)
    tc = _ids(t["to_contact"])
    check("未営業の高スコアは to_contact に入る", hi.id in tc)
    check("準備完了も to_contact に入る", lo.id in tc)
    check("営業済みは to_contact から除外", contacted.id not in tc)
    check("成約は to_contact から除外", won.id not in tc)
    check("見送りは to_contact から除外", rejected.id not in tc)

    # 成約/見送りはどのグループにも入らない
    everywhere = (
        _ids(t["to_contact"]) | _ids(t["followup"]) | _ids(t["replied"])
        | _ids(t["negotiating"]) | _ids(t["idle"])
    )
    check("成約はどのグループにも入らない", won.id not in everywhere)
    check("見送りはどのグループにも入らない", rejected.id not in everywhere)

    # to_contact はスコア順（hi が lo より前）
    order = [it["project_id"] for it in t["to_contact"]]
    check("to_contact は営業価値スコア順", order.index(hi.id) < order.index(lo.id))

    # 理由に営業価値スコアが入る
    hi_item = _find(t["to_contact"], hi.id)
    check("理由に営業価値スコアが含まれる",
          any("営業価値スコア" in r for r in hi_item["reasons"]))
    db.close()


def test_followup_timing():
    print("test_followup_timing")
    db = SessionLocal()
    maker = Maker(name="Test Maker")
    db.add(maker)
    db.commit()
    db.refresh(maker)

    recent = _mk(db, status=SalesStatus.contacted.value)      # 1日前 → idle
    d4 = _mk(db, status=SalesStatus.awaiting_reply.value)     # 4日前 → normal
    d8 = _mk(db, status=SalesStatus.contacted.value)          # 8日前 → high
    d15 = _mk(db, status=SalesStatus.contacted.value)         # 15日前 → final

    _outreach(db, maker.id, recent.id, days_ago=1)
    _outreach(db, maker.id, d4.id, days_ago=4)
    _outreach(db, maker.id, d8.id, days_ago=8)
    _outreach(db, maker.id, d15.id, days_ago=15)

    t = wf.today_tasks(db, per_group=10)
    fu = t["followup"]
    idle = t["idle"]
    fu_ids = _ids(fu)

    check("最近連絡済み（3日未満）は idle に入る", recent.id in _ids(idle))
    check("最近連絡済みは followup に入らない", recent.id not in fu_ids)
    check("4日経過は followup に入る", d4.id in fu_ids)
    check("8日経過は followup に入る", d8.id in fu_ids)
    check("15日経過は followup に入る", d15.id in fu_ids)

    check("4日経過は follow_up_level=normal", _find(fu, d4.id)["follow_up_level"] == "normal")
    check("8日経過は follow_up_level=high", _find(fu, d8.id)["follow_up_level"] == "high")
    check("15日経過は follow_up_level=final", _find(fu, d15.id)["follow_up_level"] == "final")

    check("経過日数が返る（8日）", _find(fu, d8.id)["days_since_last_outreach"] == 8)
    check("フォロー理由が入る",
          any("返信なし" in r for r in _find(fu, d15.id)["reasons"]))

    # 優先度順：final > high > normal
    order = [it["project_id"] for it in fu]
    check("フォローは優先度順（final→high→normal）",
          order.index(d15.id) < order.index(d8.id) < order.index(d4.id))
    db.close()


def test_replied_and_negotiating():
    print("test_replied_and_negotiating")
    db = SessionLocal()
    replied = _mk(db, status=SalesStatus.replied.value)
    negotiating = _mk(db, status=SalesStatus.negotiating.value)

    t = wf.today_tasks(db, per_group=10)
    check("返信ありは replied に入る", replied.id in _ids(t["replied"]))
    check("商談中は negotiating に入る", negotiating.id in _ids(t["negotiating"]))
    check("返信ありは to_contact に入らない", replied.id not in _ids(t["to_contact"]))
    check("商談中は followup に入らない", negotiating.id not in _ids(t["followup"]))
    db.close()


def main():
    test_to_contact_and_exclusions()
    test_followup_timing()
    test_replied_and_negotiating()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
