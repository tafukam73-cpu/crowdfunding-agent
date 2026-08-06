"""`scripts/requeue_contact_intelligence.py` のオフライン検証（ネットワーク不要）。

**実 Playwright / 実 Brave / 実 Gmail API / 外部 HTTP は一切呼びません。**
`create_job` / `find_active_heavy` / ジョブ状態取得 / wait ポーリング / worker 処理は
すべてフェイクへ差し替え、対象抽出・安全確認・同時実行 1 件・結果内訳を検証します。

pytest は使いません（このリポジトリの他テストと同じ自前ハーネス）。
終了コード＝失敗件数です。

実行（backend ディレクトリで）:
    python tests/test_requeue_contact_intelligence.py
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DBFILE = os.path.join(tempfile.gettempdir(), "requeue_ci_test.sqlite")
if os.path.exists(_DBFILE):
    os.remove(_DBFILE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DBFILE}"

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401  （全モデルを metadata に登録）
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.contact_intelligence_job import (  # noqa: E402
    CIJobStatus,
    CIJobType,
    ContactIntelligenceJob,
)
from app.models.project import Project  # noqa: E402
from app.models.sales_outreach import SalesOutreach  # noqa: E402
from app.services import contact_intelligence_service as ci  # noqa: E402
from app.services import contact_search_gate  # noqa: E402
from scripts import requeue_contact_intelligence as rq  # noqa: E402

Base.metadata.create_all(engine)

SCRIPT_PATH = BACKEND / "scripts" / "requeue_contact_intelligence.py"
SCRIPT_SRC = SCRIPT_PATH.read_text(encoding="utf-8")

_passed = 0
_failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"PASS- {name}")
    else:
        _failed += 1
        print(f"FAIL- {name}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
#  fixture
# --------------------------------------------------------------------------- #
def reset_db() -> None:
    db = SessionLocal()
    for model in (ContactIntelligenceJob, SalesOutreach, ContactDiscovery, Project):
        db.query(model).delete()
    db.commit()
    db.close()


def make_project(db, pid: int, *, title="P", archived=False) -> Project:
    p = Project(
        id=pid,
        title=title,
        source_site="kickstarter",
        currency="USD",
        archived_at=_now() if archived else None,
    )
    db.add(p)
    db.commit()
    return p


def make_outreach(db, pid: int, status="draft") -> None:
    db.add(SalesOutreach(project_id=pid, outreach_status=status))
    db.commit()


def make_discovery(db, pid: int, v2_status=None, email=None) -> None:
    """`primary_email` は Project ではなく ContactDiscovery が持つ（実スキーマ準拠）。"""
    disc = ContactDiscovery(project_id=pid, v2_status=v2_status)
    if email is not None:
        disc.primary_email = email
    db.add(disc)
    db.commit()


def make_job(db, pid: int, *, job_type=None, status="queued", completed_at=None) -> int:
    job = ContactIntelligenceJob(
        project_id=pid,
        job_type=job_type or CIJobType.contact_discovery_v2.value,
        status=status,
        progress=0,
        completed_at=completed_at,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job.id


def counts(db) -> dict:
    return rq.audit_counts(db)


# --------------------------------------------------------------------------- #
#  フェイク（実サービスは呼ばない）
# --------------------------------------------------------------------------- #
class FakeCreateJob:
    """`create_job` の差し替え。**外部 HTTP を伴わず queued 行だけ作る。**"""

    def __init__(self, *, from_cache=False, raise_gate=False, raise_error=False):
        self.calls: list[dict] = []
        self.from_cache = from_cache
        self.raise_gate = raise_gate
        self.raise_error = raise_error

    def __call__(self, db, project, job_type, *, override_reason=None, **kw):
        self.calls.append(
            {
                "project_id": project.id,
                "job_type": job_type,
                "override_reason": override_reason,
            }
        )
        if self.raise_gate:
            raise contact_search_gate.GateBlocked(
                {"contact_search_gate_reason": "not eligible"}
            )
        if self.raise_error:
            raise RuntimeError("boom")
        job = ContactIntelligenceJob(
            project_id=project.id,
            job_type=job_type,
            status=CIJobStatus.queued.value,
            progress=0,
            gate_override_reason=override_reason,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job, self.from_cache


def no_active(db, project_id):
    return None


def no_global_active(db):
    return None


# --------------------------------------------------------------------------- #
#  1〜3, 11: dry-run が既定
# --------------------------------------------------------------------------- #
def test_dry_run_default() -> None:
    reset_db()
    db = SessionLocal()
    make_project(db, 1)
    make_outreach(db, 1)
    before = counts(db)
    db.close()

    fake = FakeCreateJob()
    orig = ci.create_job
    ci.create_job = fake
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rq.main([])
    finally:
        ci.create_job = orig
    out = buf.getvalue()

    check("1. 引数なしは dry-run（mode 表示）", "dry-run" in out)
    check("1b. 引数なしは exit 0", code == rq.EXIT_OK)
    check("2. dry-run では create_job を呼ばない", fake.calls == [])

    db = SessionLocal()
    after = counts(db)
    db.close()
    check("3. dry-run で DB 件数が完全に不変", before == after)
    check("3b. dry-run で ci_jobs が 0 のまま", after["ci_jobs"] == 0)

    # --execute 単独（reason/confirm-count なし）は実行しない
    fake2 = FakeCreateJob()
    ci.create_job = fake2
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code2 = rq.main(["--execute"])
    finally:
        ci.create_job = orig
    check("11. --execute だけでは実行しない（非 0 終了）", code2 == rq.EXIT_USAGE)
    check("11b. --execute だけでは create_job を呼ばない", fake2.calls == [])


# --------------------------------------------------------------------------- #
#  4〜8: 対象抽出
# --------------------------------------------------------------------------- #
def test_target_selection() -> None:
    reset_db()
    db = SessionLocal()
    make_project(db, 10, title="outreach あり")
    make_outreach(db, 10, "draft")
    make_project(db, 11, title="outreach なし")          # 4. 除外される
    make_project(db, 12, title="archived", archived=True)  # 5. 除外
    make_outreach(db, 12, "sent")
    make_project(db, 13, title="v2 completed")            # 6. 除外
    make_outreach(db, 13, "replied")
    make_discovery(db, 13, v2_status="completed")
    make_project(db, 14, title="active job")              # 7. 除外
    make_outreach(db, 14, "sent")
    make_job(db, 14, status=CIJobStatus.running.value)

    targets, excluded = rq.collect_candidates(db)
    tids = [c.project_id for c in targets]
    ex = {c.project_id: c.excluded_reason for c in excluded}

    check("4. SalesOutreach ありだけ抽出（11 は対象外）", 11 not in tids and 10 in tids)
    check("5. archived を除外", 12 not in tids and ex.get(12) == rq.R_ARCHIVED)
    check("6. v2 completed を除外", 13 not in tids and ex.get(13) == rq.R_V2_COMPLETED)
    check("7. active heavy job を除外", 14 not in tids and ex.get(14) == rq.R_ACTIVE_JOB)
    check("7b. 対象は 10 のみ", tids == [10])

    # 30. 実データ条件との整合：draft / sent / replied がすべて対象語彙
    reset_db()
    for i, st in enumerate(["draft", "sent", "replied"], start=20):
        make_project(db, i)
        make_outreach(db, i, st)
    t2, _ = rq.collect_candidates(db)
    check(
        "30. Gmail 候補の実データ条件（draft/sent/replied）と整合",
        [c.project_id for c in t2] == [20, 21, 22],
    )
    check(
        "30b. outreach 状態を保持する",
        [c.outreach_statuses for c in t2] == [["draft"], ["sent"], ["replied"]],
    )
    db.close()

    # 8. project 重複を排除（sales_outreach.project_id は unique だが多重でも 1 件）
    by = rq.dedupe_rows([(5, "draft"), (5, "sent"), (6, "replied")])
    check("8. project 重複を排除", sorted(by) == [5, 6])
    check("8b. 重複行の状態をまとめる", by[5].outreach_statuses == ["draft", "sent"])
    check("8c. 重複行数を保持", by[5].outreach_rows == 2)
    check("8d. project_id が None の行は無視", rq.dedupe_rows([(None, "draft")]) == {})


# --------------------------------------------------------------------------- #
#  9, 10: limit / project-id
# --------------------------------------------------------------------------- #
def test_limit_and_project_id() -> None:
    reset_db()
    db = SessionLocal()
    for pid in (30, 31, 32):
        make_project(db, pid)
        make_outreach(db, pid)

    targets, _ = rq.collect_candidates(db)
    sel, over = rq.select_targets(targets, 1)
    check("9. --limit が効く（1 件だけ選定）", [c.project_id for c in sel] == [30])
    check("9b. 上限超過は理由付きで除外", [c.excluded_reason for c in over] == [rq.R_LIMIT] * 2)
    sel2, _ = rq.select_targets(targets, 0)
    check("9c. --limit 0 なら 0 件", sel2 == [])

    t3, ex3 = rq.collect_candidates(db, project_id=31)
    check("10. --project-id が効く", [c.project_id for c in t3] == [31])
    check(
        "10b. 非選択は skipped_not_selected",
        sorted(c.excluded_reason for c in ex3) == [rq.R_NOT_SELECTED] * 2,
    )
    db.close()


# --------------------------------------------------------------------------- #
#  12, 13: confirm-count
# --------------------------------------------------------------------------- #
def test_confirm_count() -> None:
    reset_db()
    db = SessionLocal()
    for pid in (40, 41):
        make_project(db, pid)
        make_outreach(db, pid)
    before = counts(db)
    db.close()

    orig = ci.create_job
    orig_active = ci.find_active_heavy
    ci.find_active_heavy = no_active

    # 13. 不一致 → 0 件実行・非 0 終了
    fake = FakeCreateJob()
    ci.create_job = fake
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = rq.main(
                ["--execute", "--limit", "1", "--confirm-count", "2", "--reason", "r"]
            )
    finally:
        ci.create_job = orig
    check("13. confirm-count 不一致で非 0 終了", code == rq.EXIT_USAGE)
    check("13b. confirm-count 不一致で 0 件実行", fake.calls == [])
    db = SessionLocal()
    check("13c. confirm-count 不一致で DB 不変", counts(db) == before)
    db.close()

    # 12. 一致 → 実行
    fake2 = FakeCreateJob()
    ci.create_job = fake2
    rq._global_active_heavy_orig = rq._global_active_heavy
    rq._global_active_heavy = no_global_active
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code2 = rq.main(
                ["--execute", "--limit", "1", "--confirm-count", "1",
                 "--reason", "D-3 再取得"]
            )
    finally:
        ci.create_job = orig
        ci.find_active_heavy = orig_active
        rq._global_active_heavy = rq._global_active_heavy_orig
    check("12. confirm-count 一致時だけ実行", code2 == rq.EXIT_OK and len(fake2.calls) == 1)
    check("12b. 対象は limit 内の 1 件", fake2.calls[0]["project_id"] == 40)
    check(
        "12c. v2 ジョブとして投入",
        fake2.calls[0]["job_type"] == CIJobType.contact_discovery_v2.value,
    )
    check("29. 実行理由を create_job へ渡す", fake2.calls[0]["override_reason"] == "D-3 再取得")


# --------------------------------------------------------------------------- #
#  14, 27: 同時実行 1 件 / fail closed
# --------------------------------------------------------------------------- #
def test_concurrency_and_fail_closed() -> None:
    reset_db()
    db = SessionLocal()
    for pid in (50, 51):
        make_project(db, pid)
        make_outreach(db, pid)
    targets, _ = rq.collect_candidates(db, active_heavy=no_active)

    # 14. --limit 2 以上は --wait 必須（同時実行 1 件を保証できないため）
    class A:
        execute, limit, reason, confirm_count, wait = True, 2, "r", 2, False
        poll_seconds, timeout_seconds = 15, 900

    check("14. --limit>=2 で --wait 必須", "wait" in (rq.validate_args(A()) or ""))
    A.wait = True
    check("14b. --wait があれば通る", rq.validate_args(A()) is None)
    check("14c. 既定の --limit は 1", rq.build_parser().parse_args([]).limit == 1)

    # 14d. 全体で他ジョブが動いていたら投入しない
    fake = FakeCreateJob()
    running = ContactIntelligenceJob(
        project_id=999,
        job_type=CIJobType.full_contact_intelligence.value,
        status=CIJobStatus.running.value,
        progress=0,
    )
    running.id = 777
    aborted = False
    try:
        rq.execute_targets(
            db, targets, reason="r", wait=False, poll_seconds=1, timeout_seconds=1,
            create_job=fake, active_heavy=no_active, global_active=lambda _db: running,
        )
    except rq.AbortRun:
        aborted = True
    check("14d. 全体で実行中なら停止（同時実行 1 件）", aborted)
    check("14e. その場合 create_job を呼ばない", fake.calls == [])

    # 27. active job 判定不能なら fail closed
    def boom(db_, pid):
        raise RuntimeError("db down")

    fail_closed = False
    try:
        rq.collect_candidates(db, active_heavy=boom)
    except rq.AbortRun:
        fail_closed = True
    check("27. active job 判定不能なら fail closed（抽出）", fail_closed)

    fake2 = FakeCreateJob()
    fail_closed2 = False
    try:
        rq.execute_targets(
            db, targets, reason="r", wait=False, poll_seconds=1, timeout_seconds=1,
            create_job=fake2, active_heavy=boom, global_active=no_global_active,
        )
    except rq.AbortRun:
        fail_closed2 = True
    check("27b. active job 判定不能なら fail closed（投入）", fail_closed2)
    check("27c. その場合 create_job を呼ばない", fake2.calls == [])

    # 14f. 順次投入：1 件ずつ、間に必ず排他チェックが入る
    order: list[str] = []

    def watch_global(_db):
        order.append("check")
        return None

    fake3 = FakeCreateJob()

    def watch_create(db_, project, job_type, *, override_reason=None, **kw):
        order.append(f"create:{project.id}")
        return fake3(db_, project, job_type, override_reason=override_reason)

    rq.execute_targets(
        db, targets, reason="r", wait=False, poll_seconds=1, timeout_seconds=1,
        create_job=watch_create, active_heavy=no_active, global_active=watch_global,
    )
    check(
        "14f. 1 件ずつ順次（投入の前に必ず排他チェック）",
        order == ["check", "create:50", "check", "create:51"],
    )
    db.close()


# --------------------------------------------------------------------------- #
#  15: 既存サービス経由で create_job
# --------------------------------------------------------------------------- #
def test_uses_existing_service() -> None:
    check(
        "15. 既定の create_job は contact_intelligence_service のもの",
        "create_job = create_job or ci.create_job" in SCRIPT_SRC,
    )
    check(
        "15b. 既定の active 判定は find_active_heavy",
        "active_heavy = active_heavy or ci.find_active_heavy" in SCRIPT_SRC,
    )
    check(
        "15c. job_type は既存語彙 CIJobType から取る",
        rq.JOB_TYPE in {t.value for t in CIJobType}
        and "CIJobType.contact_discovery_v2.value" in SCRIPT_SRC,
    )
    check(
        "15d. 既存 router を HTTP 経由で呼ばない",
        not re.search(r"\bapp\.routers\b", SCRIPT_SRC)
        and "TestClient" not in SCRIPT_SRC,
    )


# --------------------------------------------------------------------------- #
#  16〜24: 呼んではいけないもの（ソース走査＋実行時）
# --------------------------------------------------------------------------- #
def _code_only() -> str:
    """docstring・コメント・文字列リテラルを除いたコード本文。

    禁止語は **help 文言や禁止事項の説明文にも出てくる**ため、素の grep では
    誤検出する（実際に踏んだ）。ここでは「実際に呼んでいるか」だけを見る。
    """
    src = re.sub(r'"""(?:.|\n)*?"""', "", SCRIPT_SRC)
    src = re.sub(r"'''(?:.|\n)*?'''", "", src)
    src = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', src)
    src = re.sub(r"'(?:[^'\\\n]|\\.)*'", "''", src)
    return "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )


def test_no_forbidden_calls() -> None:
    code = _code_only()
    low = code.lower()

    # 文字列リテラルを潰したので、残るのは識別子・属性・import だけ
    check("code-only 走査が効いている（説明文を除去）", "dry-run" not in low)

    check("16. 実 Playwright を呼ばない", "playwright" not in low)
    check("16b. Chromium を直接起動しない", "chromium" not in low)
    check("16c. 探索サービスを import しない", "contact_discovery_service" not in low)
    check("17. 実 Brave API を呼ばない", "brave" not in low)
    check(
        "18. 外部 HTTP を行わない",
        not re.search(r"\b(requests|httpx|urllib|aiohttp|socket)\b", code),
    )
    check("18b. subprocess / thread を起動しない",
          not re.search(r"\b(subprocess|threading|Thread|multiprocessing)\b", code))
    check("19. Gmail API を呼ばない", "gmail" not in low)
    check(
        "20. メール送信をしない",
        not re.search(r"send_email|smtplib|\.send\(|send_message", code),
    )
    check(
        "21. メール下書きを作らない",
        not re.search(r"compose|draft|outreach_generation", low),
    )
    check(
        "22. LQE run() を呼ばない",
        "lead_qualification" not in low and not re.search(r"\brun\(", code),
    )
    check(
        "22b. pre_research / pre_outreach を再判定しない",
        "stage_pre" not in low and not re.search(r"\bqualify\(", code),
    )
    check(
        "23. 自動 archive しない",
        not re.search(r"archived_at\s*=(?!=)", code) and "archive(" not in code,
    )
    check("24. Ground Truth を変更しない", "ground_truth" not in low and "lqe_eval" not in low)
    check("24b. OUTREACH_GATE_MODE を変更しない", "outreach_gate_mode" not in low)
    check(
        "24c. 探索処理をスクリプト自身が実装しない",
        not re.search(r"def _(crawl|search|fetch|scrape)", code),
    )
    # 書き込みは create_job（既存サービス）だけに任せる
    check(
        "書き込み系は既存サービスに委譲（自前の add/commit を持たない）",
        "db.add(" not in code and "db.commit(" not in code,
    )
    check(
        "gate は persist=False（判定を保存しない）",
        "persist=True" not in SCRIPT_SRC and "persist=False" in SCRIPT_SRC,
    )


# --------------------------------------------------------------------------- #
#  25: JSON 出力にメールアドレスなし
# --------------------------------------------------------------------------- #
def test_json_has_no_email() -> None:
    reset_db()
    db = SessionLocal()
    make_project(db, 60, title="秘密")
    make_outreach(db, 60)
    make_discovery(db, 60, email="secret-owner@example.com")
    db.close()

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = rq.main(["--json"])
    out = buf.getvalue()
    payload = json.loads(out)

    check("25. JSON にメールアドレスが含まれない", "secret-owner@example.com" not in out)
    check("25b. JSON に @ を含むアドレス表記がない", not re.search(r"[\w.]+@[\w.]+", out))
    check(
        "25c. 代わりに有無だけを出す",
        payload["targets"][0]["has_primary_email"] is True,
    )
    check("25d. JSON は dry-run で jobs_created 0", payload["jobs_created"] == 0)
    check("25e. JSON 出力は exit 0", code == rq.EXIT_OK)
    check(
        "25f. API キー / Cookie を出さない",
        "api_key" not in out.lower() and "cookie" not in out.lower(),
    )
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        rq.main([])
    check("25h. 表形式出力にもアドレスなし", "secret-owner@example.com" not in buf2.getvalue())


# --------------------------------------------------------------------------- #
#  26: 失敗・skip の内訳
# --------------------------------------------------------------------------- #
def test_result_breakdown() -> None:
    reset_db()
    db = SessionLocal()
    for pid in (70, 71, 72):
        make_project(db, pid)
        make_outreach(db, pid)
    targets, _ = rq.collect_candidates(db, active_heavy=no_active)

    # gate 不合格
    res_gate = rq.execute_targets(
        db, targets[:1], reason="r", wait=False, poll_seconds=1, timeout_seconds=1,
        create_job=FakeCreateJob(raise_gate=True), active_heavy=no_active,
        global_active=no_global_active,
    )
    check("26. gate 不合格を failed_gate_blocked として記録", res_gate[0]["result"] == rq.R_GATE_BLOCKED)
    check("26b. gate 不合格を成功扱いにしない", res_gate[0]["job_id"] is None)

    # 想定外エラー：1 件の失敗で全体をクラッシュさせない
    res_err = rq.execute_targets(
        db, targets, reason="r", wait=False, poll_seconds=1, timeout_seconds=1,
        create_job=FakeCreateJob(raise_error=True), active_heavy=no_active,
        global_active=no_global_active,
    )
    check("26c. 1 件の失敗で全体を止めない", len(res_err) == 3)
    check("26d. 例外は failed_error として集計", all(r["result"] == rq.R_ERROR for r in res_err))
    check(
        "26e. stack trace を結果へ入れない",
        all("Traceback" not in str(r.get("detail", "")) for r in res_err),
    )

    # キャッシュ再利用は enqueued にしない
    res_cache = rq.execute_targets(
        db, targets[:1], reason="r", wait=False, poll_seconds=1, timeout_seconds=1,
        create_job=FakeCreateJob(from_cache=True), active_heavy=no_active,
        global_active=no_global_active,
    )
    check("26f. 24h キャッシュ再利用を enqueued にしない", res_cache[0]["result"] == rq.R_CACHE)

    # 投入直前に active を検出したら skip
    active_job = ContactIntelligenceJob(project_id=70, job_type=rq.JOB_TYPE,
                                        status=CIJobStatus.running.value, progress=0)
    active_job.id = 888
    fake = FakeCreateJob()
    res_active = rq.execute_targets(
        db, targets[:1], reason="r", wait=False, poll_seconds=1, timeout_seconds=1,
        create_job=fake, active_heavy=lambda d, p: active_job,
        global_active=no_global_active,
    )
    check("26g. 投入直前の active を skip", res_active[0]["result"] == rq.R_ACTIVE_JOB)
    check("26h. その場合 create_job を呼ばない", fake.calls == [])

    summary = rq.summarize(res_err + res_cache + res_active, [])
    check("26i. 内訳に failed が出る", summary["failed"] == 3)
    check("26j. 内訳に cache_reused が出る", summary["cache_reused"] == 1)
    check("26k. 内訳に active_job_skipped が出る", summary["active_job_skipped"] == 1)
    check("26l. enqueued は 0", summary["enqueued"] == 0)
    db.close()


# --------------------------------------------------------------------------- #
#  wait / timeout（実 sleep なし）
# --------------------------------------------------------------------------- #
def test_wait_and_timeout() -> None:
    reset_db()
    db = SessionLocal()
    for pid in (80, 81):
        make_project(db, pid)
        make_outreach(db, pid)
    targets, _ = rq.collect_candidates(db, active_heavy=no_active)

    slept: list[int] = []

    def fake_sleep(n):
        slept.append(n)

    # 完了しないジョブ → タイムアウトで後続を開始しない
    fake = FakeCreateJob()
    aborted = False
    try:
        rq.execute_targets(
            db, targets, reason="r", wait=True, poll_seconds=5, timeout_seconds=10,
            create_job=fake, active_heavy=no_active, global_active=no_global_active,
            sleep=fake_sleep,
        )
    except rq.AbortRun:
        aborted = True
    check("timeout: 終了しないジョブで停止する", aborted)
    check("timeout: 後続の案件を勝手に開始しない", len(fake.calls) == 1)
    check("timeout: 実 sleep を使わずポーリング間隔で待つ", slept == [5, 5])

    # 完了するジョブ → 次へ進む
    reset_db()
    for pid in (82, 83):
        make_project(db, pid)
        make_outreach(db, pid)
    targets2, _ = rq.collect_candidates(db, active_heavy=no_active)

    class CompletingCreateJob(FakeCreateJob):
        def __call__(self, db_, project, job_type, *, override_reason=None, **kw):
            job, cached = super().__call__(
                db_, project, job_type, override_reason=override_reason
            )
            job.status = CIJobStatus.completed.value
            db_.commit()
            return job, cached

    fake2 = CompletingCreateJob()
    res = rq.execute_targets(
        db, targets2, reason="r", wait=True, poll_seconds=5, timeout_seconds=10,
        create_job=fake2, active_heavy=no_active, global_active=no_global_active,
        sleep=fake_sleep,
    )
    check("wait: 完了したら次へ進む", len(fake2.calls) == 2)
    check("wait: 結果は enqueued", all(r["result"] == rq.R_ENQUEUED for r in res))
    check("wait: 最終 status を記録", all(r["job_status"] == "completed" for r in res))
    db.close()


# --------------------------------------------------------------------------- #
#  28: 件数監査
# --------------------------------------------------------------------------- #
def test_audit_counts() -> None:
    reset_db()
    db = SessionLocal()
    make_project(db, 90)
    make_outreach(db, 90)
    make_project(db, 91, archived=True)
    make_discovery(db, 90)
    make_job(db, 90, status=CIJobStatus.completed.value)
    c = counts(db)
    db.close()

    check("28. projects 件数を監査", c["projects"] == 2)
    check("28b. archived 件数を監査", c["projects_archived"] == 1)
    check("28c. contact_discoveries 件数を監査", c["contact_discoveries"] == 1)
    check("28d. CI jobs 件数を監査", c["ci_jobs"] == 1)

    buf = io.StringIO()
    with redirect_stdout(buf):
        rq.main(["--json"])
    payload = json.loads(buf.getvalue())
    check("28e. 実行前後の件数を出力", "counts_before" in payload and "counts_after" in payload)
    check("28f. dry-run で件数が一致", payload["counts_before"] == payload["counts_after"])
    check("28g. 対象 project ID 一覧を出力", payload["target_project_ids"] == [90])


# --------------------------------------------------------------------------- #
#  29: 実行理由の記録
# --------------------------------------------------------------------------- #
def test_reason_recorded() -> None:
    reset_db()
    db = SessionLocal()
    make_project(db, 95)
    make_outreach(db, 95)
    db.close()

    class A:
        execute, limit, reason, confirm_count, wait = True, 1, "", 1, False
        poll_seconds, timeout_seconds = 15, 900

    check("29b. --reason 空文字は拒否", rq.validate_args(A()) is not None)
    A.reason = None
    check("29c. --reason 省略は拒否", rq.validate_args(A()) is not None)
    A.reason, A.confirm_count = "理由", None
    check("29d. --confirm-count 省略は拒否", rq.validate_args(A()) is not None)

    db = SessionLocal()
    targets, _ = rq.collect_candidates(db, active_heavy=no_active)
    fake = FakeCreateJob()
    rq.execute_targets(
        db, targets, reason="D-3 証跡再取得", wait=False, poll_seconds=1,
        timeout_seconds=1, create_job=fake, active_heavy=no_active,
        global_active=no_global_active,
    )
    job = db.query(ContactIntelligenceJob).order_by(
        ContactIntelligenceJob.id.desc()
    ).first()
    check("29e. 理由がジョブへ記録される", job.gate_override_reason == "D-3 証跡再取得")
    db.close()


# --------------------------------------------------------------------------- #
#  ドキュメント（--help / docstring）
# --------------------------------------------------------------------------- #
def test_documentation() -> None:
    doc = rq.__doc__ or ""
    for phrase, label in [
        ("既定は dry-run", "既定は dry-run"),
        ("--execute", "--execute なしではジョブを作らない"),
        ("ワーカー側", "外部 HTTP はワーカー側で発生する"),
        ("人の承認", "実行前に人の承認が必要"),
        ("1 件", "1 件ずつ実行"),
        ("enforce", "enforce を有効化しない"),
        ("LQE", "LQE 再判定は別工程"),
        ("推測で補完しない", "証跡が取れなくても推測で補完しない"),
    ]:
        check(f"doc: docstring に「{label}」", phrase in doc)

    helptext = rq.build_parser().format_help()
    for phrase in ["dry-run", "--execute", "ワーカー", "承認", "enforce", "推測"]:
        check(f"help: --help に「{phrase}」", phrase in helptext)


def main() -> int:
    print("=== requeue_contact_intelligence ===")
    test_dry_run_default()
    test_target_selection()
    test_limit_and_project_id()
    test_confirm_count()
    test_concurrency_and_fail_closed()
    test_uses_existing_service()
    test_no_forbidden_calls()
    test_json_has_no_email()
    test_result_breakdown()
    test_wait_and_timeout()
    test_audit_counts()
    test_reason_recorded()
    test_documentation()
    print(f"\n{_passed} passed / {_failed} failed")
    return _failed


if __name__ == "__main__":
    sys.exit(main())
