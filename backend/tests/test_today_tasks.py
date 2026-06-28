"""「今日やること」分類（workflow_service.today_tasks）の検証（DB は in-memory SQLite）。

営業状況で 4 グループ（営業 / フォローアップ / 返信あり / 商談中）に正しく分類し、
営業対象サイト以外（Makuake/GreenFunding）は含めないことを確認する。

実行（backend ディレクトリで）:
    python tests/test_today_tasks.py
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


def main() -> int:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all([
        Project(title="Eco Kitchen", source_site="ulule", currency="EUR",
                sales_status="not_started", latest_score=90),
        Project(title="Ready One", source_site="kickstarter", currency="USD",
                sales_status="ready", latest_score=50),
        Project(title="Smart Bottle", source_site="kickstarter", currency="USD",
                sales_status="contacted", latest_score=70),
        Project(title="Wait Reply", source_site="indiegogo", currency="USD",
                sales_status="awaiting_reply", latest_score=65),
        Project(title="Nordic Lamp", source_site="indiegogo", currency="USD",
                sales_status="replied", latest_score=80),
        Project(title="Eco Backpack", source_site="wadiz", currency="USD",
                sales_status="negotiating", latest_score=60),
        # 営業対象外サイト（含めない）
        Project(title="Makuake Hit", source_site="makuake", currency="JPY",
                sales_status="not_started", latest_score=99),
    ])
    db.commit()

    t = workflow_service.today_tasks(db)
    tc = [x["title"] for x in t["to_contact"]]
    fu = [x["title"] for x in t["followup"]]
    rp = [x["title"] for x in t["replied"]]
    ng = [x["title"] for x in t["negotiating"]]

    check("営業（未営業/準備完了）", set(tc) == {"Eco Kitchen", "Ready One"})
    check("フォローアップ（営業済み/返信待ち）", set(fu) == {"Smart Bottle", "Wait Reply"})
    check("返信あり", rp == ["Nordic Lamp"])
    check("商談中", ng == ["Eco Backpack"])
    check("営業対象外サイトは含めない", "Makuake Hit" not in tc)
    check("各 item に latest_score を含む", all("latest_score" in x for x in t["to_contact"]))
    # スコア降順（to_contact は Eco Kitchen 90 が先）
    check("グループ内はスコア降順", tc[0] == "Eco Kitchen")

    db.close()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
