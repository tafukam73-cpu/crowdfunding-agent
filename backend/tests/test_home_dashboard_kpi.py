"""ホーム（Home Dashboard v2）KPI 集計のオフライン検証（ネットワーク不要・SQLite）。

/sales/dashboard に追加した contract_agreed_count（契約目前）と selling_count
（販売中）が、営業状況ごとに正しく数えられることを確認する。

- 契約目前は contract_agreed のみ（旧 won も後方互換で含む／輸入準備以降は含めない）
- 販売中は selling のみ
- 既存フィールド（won_count 等）は後方互換のまま
- 営業対象外（アーカイブ）案件は KPI から除外される

実行（backend ディレクトリで）:
    python tests/test_home_dashboard_kpi.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "home_dashboard_kpi_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"
os.environ["TESTING"] = "true"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from datetime import datetime, timezone  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
from app.models.project import Project  # noqa: E402
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


def _mk(db, sales_status: str, *, archived: bool = False) -> Project:
    global _seq
    _seq += 1
    p = Project(
        title=f"P{_seq}",
        source_site="kickstarter",
        source_url=f"https://ex.com/{_seq}",
        sales_status=sales_status,
        archived_at=datetime.now(timezone.utc) if archived else None,
    )
    db.add(p)
    db.commit()
    return p


def test_kpi_counts():
    print("test_kpi_counts")
    db = SessionLocal()
    # 返信待ち 2 / 商談中 1 / 契約合意 2（うち 1 件は旧 won）/ 輸入準備 1 / 販売中 3
    for _ in range(2):
        _mk(db, "awaiting_reply")
    _mk(db, "negotiating")
    _mk(db, "contract_agreed")
    _mk(db, "won")
    _mk(db, "import_prep")
    for _ in range(3):
        _mk(db, "selling")

    d = wf.dashboard_summary(db)
    check("返信待ち=2", d["awaiting_reply_count"] == 2)
    check("商談中=1", d["negotiating_count"] == 1)
    check("契約目前=2（contract_agreed + 旧 won）", d["contract_agreed_count"] == 2)
    check("契約目前に輸入準備・販売中は含めない", d["contract_agreed_count"] == 2)
    check("販売中=3", d["selling_count"] == 3)
    # 後方互換：won_count は契約以降（契約合意〜販売中）の合計 = 2 + 1 + 3
    check("won_count は契約以降の合計=6（後方互換）", d["won_count"] == 6)
    db.close()


def test_archived_excluded():
    print("test_archived_excluded")
    db = SessionLocal()
    before = wf.dashboard_summary(db)
    _mk(db, "selling", archived=True)
    _mk(db, "contract_agreed", archived=True)
    after = wf.dashboard_summary(db)
    check(
        "営業対象外は販売中 KPI に含めない",
        after["selling_count"] == before["selling_count"],
    )
    check(
        "営業対象外は契約目前 KPI に含めない",
        after["contract_agreed_count"] == before["contract_agreed_count"],
    )
    db.close()


def test_api_layer():
    print("test_api_layer")
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    r = c.get("/sales/dashboard")
    check("GET /sales/dashboard 200", r.status_code == 200)
    body = r.json()
    check("contract_agreed_count を返す", "contract_agreed_count" in body)
    check("selling_count を返す", "selling_count" in body)
    check(
        "既存フィールドは温存（後方互換）",
        {"ready_count", "today_count", "awaiting_reply_count", "won_count"}
        <= set(body),
    )


def main():
    test_kpi_counts()
    test_archived_excluded()
    test_api_layer()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
