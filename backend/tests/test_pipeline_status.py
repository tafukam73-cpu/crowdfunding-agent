"""営業パイプライン（sales_status 状態機械）のオフライン検証（ネットワーク不要・SQLite）。

- 厳密な状態遷移ガード（許可/不許可）
- 状態変更履歴（project_status_events）の記録と change_source
- 営業アクションによる自動前進（送信→初回営業済み / 返信→返信あり）
- 一括更新（不正遷移はスキップ）
- won（非推奨）→ contract_agreed への正規化
- API レベル（PATCH 409 / status-events / 一括）

実行（backend ディレクトリで）:
    python tests/test_pipeline_status.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "pipeline_status_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"
os.environ["TESTING"] = "true"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.project import (  # noqa: E402
    Project,
    SalesStatus,
    can_transition_sales_status,
    normalize_sales_status,
)
from app.models.project_status_event import StatusChangeSource  # noqa: E402
from app.services import project_service as ps  # noqa: E402
from app.services import sales_outreach_service as sos  # noqa: E402

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


def _mk(db, *, sales_status: str = "not_started") -> Project:
    global _seq
    _seq += 1
    p = Project(
        title=f"P{_seq}",
        source_site="kickstarter",
        source_url=f"https://ex.com/{_seq}",
        sales_status=sales_status,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def test_pure_transition_map():
    print("test_pure_transition_map")
    check("not_started→contacted 許可", can_transition_sales_status("not_started", "contacted"))
    check("contacted→selling 不許可", not can_transition_sales_status("contacted", "selling"))
    check("negotiating→contract_agreed 許可", can_transition_sales_status("negotiating", "contract_agreed"))
    check("contract_agreed→import_prep 許可", can_transition_sales_status("contract_agreed", "import_prep"))
    check("selling→closed 許可", can_transition_sales_status("selling", "closed"))
    check("同一状態は許可（冪等）", can_transition_sales_status("selling", "selling"))
    check("won正規化→import_prep 許可", can_transition_sales_status("won", "import_prep"))
    check("normalize won→contract_agreed", normalize_sales_status("won") == "contract_agreed")


def test_guard_and_history():
    print("test_guard_and_history")
    db = SessionLocal()
    p = _mk(db)
    ps.update_sales_status(db, p, SalesStatus.contacted, source="manual")
    check("正常遷移で状態が進む", p.sales_status == "contacted")

    raised = False
    try:
        ps.update_sales_status(db, p, SalesStatus.selling)
    except ps.InvalidStatusTransition:
        raised = True
    check("不正遷移は InvalidStatusTransition", raised)
    db.refresh(p)
    check("不正遷移では状態が変わらない", p.sales_status == "contacted")

    # 履歴：1件（not_started→contacted, manual）だけ。不正遷移は記録しない。
    evs = ps.list_status_events(db, p.id)
    check("履歴は1件", len(evs) == 1)
    check("履歴 from/to/source", evs[0].from_status == "not_started"
          and evs[0].to_status == "contacted"
          and evs[0].change_source == "manual")

    # 同一状態は no-op（履歴も増えない）
    ps.update_sales_status(db, p, SalesStatus.contacted)
    check("同一状態はno-op（履歴増えない）", len(ps.list_status_events(db, p.id)) == 1)
    db.close()


def test_full_pipeline_path():
    print("test_full_pipeline_path")
    db = SessionLocal()
    p = _mk(db)
    path = ["ready", "contacted", "awaiting_reply", "replied", "negotiating",
            "contract_agreed", "import_prep", "jp_cf_prep", "selling", "closed"]
    ok = True
    for st in path:
        try:
            ps.update_sales_status(db, p, SalesStatus(st), source="manual")
        except ps.InvalidStatusTransition:
            ok = False
            break
    check("未着手→…→販売中→終了 まで全遷移が許可される", ok and p.sales_status == "closed")
    check("履歴が遷移回数ぶん記録される", len(ps.list_status_events(db, p.id)) == len(path))
    db.close()


def test_won_normalization_row():
    print("test_won_normalization_row")
    db = SessionLocal()
    p = _mk(db, sales_status="won")  # 旧データ
    check("is_archived 前提: normalize で contract_agreed 扱い",
          normalize_sales_status(p.sales_status) == "contract_agreed")
    # won(=contract_agreed) から import_prep へ進める
    ps.update_sales_status(db, p, SalesStatus.import_prep, source="manual")
    check("won行から import_prep へ進める", p.sales_status == "import_prep")
    ev = ps.list_status_events(db, p.id)[0]
    check("履歴の from は正規化されて contract_agreed", ev.from_status == "contract_agreed")
    db.close()


def test_sync_forward_only():
    print("test_sync_forward_only")
    db = SessionLocal()
    # not_started → contacted（gmail 自動）
    p = _mk(db)
    ps.sync_sales_status(db, p, SalesStatus.contacted.value,
                         source=StatusChangeSource.gmail.value,
                         only_from={"not_started", "ready"})
    check("未着手→初回営業済み（自動前進）", p.sales_status == "contacted")
    ev = ps.list_status_events(db, p.id)[0]
    check("自動前進の change_source=gmail", ev.change_source == "gmail")

    # 既に先の状態（negotiating）は後退・上書きしない
    p2 = _mk(db, sales_status="negotiating")
    ps.sync_sales_status(db, p2, SalesStatus.contacted.value,
                         source=StatusChangeSource.gmail.value,
                         only_from={"not_started", "ready"})
    check("先の状態は自動同期で後退しない", p2.sales_status == "negotiating")
    db.close()


def test_mark_sent_and_reply_autosync():
    print("test_mark_sent_and_reply_autosync")
    db = SessionLocal()
    p = _mk(db)
    sos.mark_sent(db, p, language="en", subject="s", body="b", recipient="x@e.com")
    db.refresh(p)
    check("送信済み登録で sales_status→初回営業済み", p.sales_status == "contacted")
    check("送信の履歴 change_source=gmail",
          any(e.change_source == "gmail" for e in ps.list_status_events(db, p.id)))

    sos.reply_confirm(db, p, incoming_body="Thanks, interested!")
    db.refresh(p)
    check("返信登録で sales_status→返信あり", p.sales_status == "replied")
    check("返信の履歴 change_source=reply",
          any(e.change_source == "reply" for e in ps.list_status_events(db, p.id)))
    db.close()


def test_bulk_update():
    print("test_bulk_update")
    db = SessionLocal()
    a = _mk(db)                       # not_started → contacted 可
    b = _mk(db, sales_status="selling")  # selling → contacted 不可（スキップ）
    updated, skipped = ps.bulk_update_sales_status(
        db, [a.id, b.id], SalesStatus.contacted, source="manual"
    )
    check("一括: 更新1件", updated == 1)
    check("一括: スキップ1件（不正遷移）", skipped == [b.id])
    db.refresh(a); db.refresh(b)
    check("a は contacted", a.sales_status == "contacted")
    check("b は変わらず selling", b.sales_status == "selling")
    db.close()


def test_api_layer():
    print("test_api_layer")
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    db = SessionLocal()
    p = _mk(db)
    pid = p.id
    db.close()

    r1 = c.patch(f"/projects/{pid}/sales-status", json={"sales_status": "contacted"})
    check("API 正常遷移 200", r1.status_code == 200)
    r2 = c.patch(f"/projects/{pid}/sales-status", json={"sales_status": "selling"})
    check("API 不正遷移 409", r2.status_code == 409)
    ev = c.get(f"/projects/{pid}/status-events").json()["items"]
    check("API 履歴が取得できる（1件）", len(ev) == 1)
    rb = c.post("/projects/sales-status",
                json={"ids": [pid], "sales_status": "awaiting_reply"})
    check("API 一括 200 / updated=1", rb.status_code == 200 and rb.json()["updated"] == 1)


def main():
    test_pure_transition_map()
    test_guard_and_history()
    test_full_pipeline_path()
    test_won_normalization_row()
    test_sync_forward_only()
    test_mark_sent_and_reply_autosync()
    test_bulk_update()
    test_api_layer()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
