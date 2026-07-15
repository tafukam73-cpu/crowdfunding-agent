"""実 PostgreSQL 検証：フェーズの外部処理中に idle in transaction=0 を実測する。

実際の `run_web_research` 経路を通し、外部コア（web_research）だけを「6 秒スリープ」に
差し替える。別コネクションから、その実行セッションの backend pid の state を 0.2 秒間隔で
サンプリングし、外部処理（スリープ）中に一度も `idle in transaction` にならないことを
確認する。使い捨ての Project/ContactDiscovery を作り、最後に必ず削除する（実データ非破壊）。

実行（backend コンテナ内。DATABASE_URL は db を指す）:
    docker exec cfagent-backend python tests/verify_session_lifecycle_pg.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("APP_COMPONENT", "cfagent-ci-verify")

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.services import web_research_service  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    print(f"  {'ok  ' if cond else 'FAIL'}- {name}")
    if cond:
        _passed += 1
    else:
        _failed += 1


SLEEP_SECONDS = 6.0


def main() -> int:
    if SessionLocal.kw.get("bind") is None and "postgresql" not in os.environ.get(
        "DATABASE_URL", ""
    ):
        print("SKIP: not PostgreSQL")
        return 0

    # 使い捨ての Project + ContactDiscovery を作成
    setup = SessionLocal()
    proj = Project(title="__ci_verify_throwaway__", source_site="kickstarter",
                   maker_url="https://example.com")
    setup.add(proj)
    setup.commit()
    setup.refresh(proj)
    pid_project = proj.id
    row = ContactDiscovery(project_id=pid_project, status="completed")
    setup.add(row)
    setup.commit()
    setup.close()

    window = {"active": False}
    samples: list[str] = []

    def fake_core(project, research, *, fetch_fn=None, search_fn=None, progress_cb=None):
        # 外部処理を模擬（この間 DB セッション／トランザクションを保持しないことを検証）
        window["active"] = True
        time.sleep(SLEEP_SECONDS)
        window["active"] = False
        # 保存させないため最小の結果を返す（web_* 保存はするが軽い）
        return {k: None for k in (
            "search_provider", "search_diagnostics", "debug_counts", "research_flow",
            "keyword_candidates", "generated_queries", "searched_queries",
            "search_results", "searched_urls", "candidate_pages", "discovered_emails",
            "discovered_forms", "discovered_socials", "discovered_pdfs",
            "primary_email", "primary_contact_form_url", "recommended_channel",
            "confidence_score", "evidence_summary", "notes", "official_site_url",
        )}

    run_backend_pid = {"pid": None}

    def run_phase():
        db = SessionLocal()
        run_backend_pid["pid"] = db.execute(text("SELECT pg_backend_pid()")).scalar()
        proj_obj = db.get(Project, pid_project)
        orig = web_research_service.web_research
        web_research_service.web_research = fake_core
        try:
            web_research_service.run_web_research(db, proj_obj)
        finally:
            web_research_service.web_research = orig
            db.close()

    t = threading.Thread(target=run_phase)
    t.start()

    # 外部スリープ窓が開くのを待ち、その間、実行セッション pid の state をサンプリング
    while run_backend_pid["pid"] is None or not window["active"]:
        time.sleep(0.05)
        if not t.is_alive():
            break
    # 監視は SessionLocal のプールとは別（NullPool）にして、実行セッションが返却した
    # コネクションを監視側が再利用してしまう（state=active に見える）のを避ける。
    mon_engine = create_engine(settings.database_url, poolclass=NullPool)
    idle_in_txn_hits = 0
    total = 0
    while window["active"]:
        with mon_engine.connect() as c:
            st = c.execute(
                text("SELECT state FROM pg_stat_activity WHERE pid = :p"),
                {"p": run_backend_pid["pid"]},
            ).scalar()
        total += 1
        samples.append(st or "gone")
        if st == "idle in transaction":
            idle_in_txn_hits += 1
        time.sleep(0.2)
    mon_engine.dispose()
    t.join()

    print(f"  sampled {total} times during external sleep; states={set(samples)}")
    check("外部処理中の idle in transaction サンプル数 = 0", idle_in_txn_hits == 0)
    check("外部処理中に実行セッションを観測できた（サンプル>0）", total > 0)
    check("外部処理中の state は idle（接続はプールへ返却）",
          all(s in ("idle", "gone", None) for s in samples))

    # クリーンアップ（使い捨てデータのみ削除）
    cleanup = SessionLocal()
    cleanup.query(ContactDiscovery).filter(
        ContactDiscovery.project_id == pid_project
    ).delete()
    cleanup.query(Project).filter(Project.id == pid_project).delete()
    cleanup.commit()
    cleanup.close()

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
