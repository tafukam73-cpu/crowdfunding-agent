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


#: 純粋関数テスト用の実 campaign_url（`projects.source_url` は unique なので、
#: DB へ入れるときは project ごとに別 URL を使う）。
REAL_CAMPAIGN = "https://www.kickstarter.com/projects/acme-lab/real-widget"

_UNSET = object()


def make_project(
    db,
    pid: int,
    *,
    title="P",
    archived=False,
    source_url=_UNSET,
    maker_url=None,
) -> Project:
    if source_url is _UNSET:
        source_url = f"https://www.kickstarter.com/projects/acme-lab/widget-{pid}"
    p = Project(
        id=pid,
        title=title,
        source_site="kickstarter",
        currency="USD",
        source_url=source_url,
        maker_url=maker_url,
        archived_at=_now() if archived else None,
    )
    db.add(p)
    db.commit()
    return p


def make_outreach(db, pid: int, status="draft") -> None:
    db.add(SalesOutreach(project_id=pid, outreach_status=status))
    db.commit()


def make_discovery(db, pid: int, v2_status=None, email=None, official_site=None) -> None:
    """`primary_email` は Project ではなく ContactDiscovery が持つ（実スキーマ準拠）。"""
    disc = ContactDiscovery(project_id=pid, v2_status=v2_status)
    if email is not None:
        disc.primary_email = email
    if official_site is not None:
        disc.official_site_url = official_site
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
    # 探索サービスからは **判定ヘルパだけ** を借りる（探索の実行関数は呼ばない）。
    check(
        "16c. 探索の実行関数を呼ばない",
        not re.search(r"run_discovery|run_contact_discovery|run_ai_research|run_web_research",
                      code),
    )
    check(
        "16d. contact_discovery_service から借りるのは判定ヘルパのみ",
        re.findall(r"from app\.services\.contact_discovery_service import (\w+)", SCRIPT_SRC)
        == ["is_dummy_domain"],
    )
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


# --------------------------------------------------------------------------- #
#  ダミー/テストデータの除外（純粋関数）
# --------------------------------------------------------------------------- #
def test_dummy_signals_pure() -> None:
    def sig(**kw):
        base = {"campaign_url": None, "official_site_url": None, "email_domain": None}
        base.update(kw)
        return rq.dummy_or_test_signals(**base)

    check("d1. example.com の公式サイト＋campaign_urlなし → ダミー",
          sig(official_site_url="https://example.com") != [])
    check("d2. example.com のメール＋campaign_urlなし → ダミー",
          sig(email_domain="example.com") != [])
    check("d3. maker.example.com を除外",
          sig(official_site_url="https://maker.example.com") != [])
    check("d4. example.org を除外", sig(official_site_url="https://example.org") != [])
    check("d4b. example.net を除外", sig(official_site_url="https://example.net") != [])
    check("d4c. example.net メールを除外", sig(email_domain="example.net") != [])
    check("d5. localhost を除外", sig(official_site_url="http://localhost:3000") != [])
    check("d5b. .invalid を除外", sig(official_site_url="https://acme.invalid") != [])
    check("d5c. localhost メールを除外", sig(email_domain="localhost") != [])

    # 単独条件では除外しない（安全側）
    check("d6. campaign_url があれば example.com でもダミー扱いしない",
          sig(campaign_url=REAL_CAMPAIGN, official_site_url="https://example.com") == [])
    check("d6b. campaign_url があればダミーメールでも除外しない",
          sig(campaign_url=REAL_CAMPAIGN, email_domain="example.com") == [])
    check("d7. campaign_url が無いだけでは除外しない（他情報なし）", sig() == [])
    check("d7b. 実ドメインの公式サイトなら除外しない",
          sig(official_site_url="https://acme-lab.co.jp") == [])
    check("d7c. 実ドメインのメールなら除外しない", sig(email_domain="acme-lab.co.jp") == [])
    check("d7d. example-brand.com のような正規ドメインを巻き込まない",
          sig(official_site_url="https://example-brand.com") == [])

    check("d8. 根拠ラベルを返す（属性名つき）",
          "official_site:" in sig(official_site_url="https://example.com")[0])
    check("d8b. メール由来の根拠はドメイン種別のみ",
          sig(email_domain="example.com") == ["email_domain:reserved"])


def test_research_seed_pure() -> None:
    def seed(**kw):
        base = {"campaign_url": None, "maker_url": None, "official_site_url": None}
        base.update(kw)
        return rq.research_seed(**base)

    check("s1. campaign_url が最優先", seed(campaign_url=REAL_CAMPAIGN,
          maker_url="https://acme-lab.co.jp") == "campaign_url")
    check("s2. campaign_url が無ければ maker_url",
          seed(maker_url="https://acme-lab.co.jp") == "maker_url")
    check("s3. 次に検証済み公式サイト",
          seed(official_site_url="https://acme-lab.co.jp") == "official_site")
    check("s4. すべて無ければ None", seed() is None)
    check("s5. プレースホルダー URL は起点にしない",
          seed(official_site_url="https://maker.example.com") is None)
    check("s5b. localhost は起点にしない", seed(maker_url="http://localhost") is None)
    check("s6. ダミー公式サイトでも実 maker_url があれば起点になる",
          seed(maker_url="https://acme-lab.co.jp",
               official_site_url="https://example.com") == "maker_url")


def test_dummy_exclusion_in_collect() -> None:
    reset_db()
    db = SessionLocal()

    # ダミー: campaign_url なし + example.com 公式サイト + example.com メール
    make_project(db, 200, title="Editable Gadget", source_url=None)
    make_outreach(db, 200)
    make_discovery(db, 200, email="a@example.com",
                   official_site="https://maker.example.com")

    # ダミー: campaign_url なし + example.org 公式サイトのみ
    make_project(db, 201, title="Send Gadget", source_url=None)
    make_outreach(db, 201)
    make_discovery(db, 201, official_site="https://example.org")

    # 実案件: campaign_url あり（example.com のメールを持っていても除外しない）
    make_project(db, 202, title="実案件")
    make_outreach(db, 202)
    make_discovery(db, 202, email="a@example.com")

    # 実案件: campaign_url なしだが maker_url あり
    make_project(db, 203, title="maker_url あり", source_url=None,
                 maker_url="https://acme-lab.co.jp")
    make_outreach(db, 203)

    # 実案件: campaign_url なしだが検証済み公式サイトあり
    make_project(db, 204, title="official site あり", source_url=None)
    make_outreach(db, 204)
    make_discovery(db, 204, official_site="https://acme-lab.co.jp")

    # 起点なし: campaign_url も maker_url も公式サイトも無い（メールだけある）
    make_project(db, 205, title="起点なし", source_url=None)
    make_outreach(db, 205)
    make_discovery(db, 205, email="a@acme-lab.co.jp")

    # 起点なし: maker_name だけ（メールも無い）
    p = make_project(db, 206, title="名前だけ", source_url=None)
    p.maker_name = "EditCo"
    db.commit()
    make_outreach(db, 206)

    targets, excluded = rq.collect_candidates(db, active_heavy=no_active)
    tids = [c.project_id for c in targets]
    ex = {c.project_id: c.excluded_reason for c in excluded}

    check("1. example.com 公式サイト＋campaign_urlなしを除外",
          ex.get(200) == rq.R_DUMMY)
    check("2. example.com メール＋campaign_urlなしを除外（同一案件で検出）",
          "email_domain:reserved" in
          next(c for c in excluded if c.project_id == 200).dummy_signals)
    check("3. maker.example.com を除外",
          "official_site:" in
          next(c for c in excluded if c.project_id == 200).dummy_signals[0])
    check("4. example.org を除外", ex.get(201) == rq.R_DUMMY)
    check("7. campaign_url ありの実案件は除外しない", 202 in tids)
    check("7b. campaign_url ありなら seed=campaign_url",
          next(c for c in targets if c.project_id == 202).seed_kind == "campaign_url")
    check("8. maker_url ありの正当案件は対象にする", 203 in tids)
    check("8b. その seed は maker_url",
          next(c for c in targets if c.project_id == 203).seed_kind == "maker_url")
    check("9. 検証済み公式サイトありの正当案件は対象にする", 204 in tids)
    check("9b. その seed は official_site",
          next(c for c in targets if c.project_id == 204).seed_kind == "official_site")
    check("6. campaign_urlなし＋起点なしを除外", ex.get(205) == rq.R_NO_SEED)
    check("10. maker_name だけでは実行対象にしない", ex.get(206) == rq.R_NO_SEED)
    check("10b. primary_email は起点として認めない",
          205 not in tids and next(c for c in excluded
                                   if c.project_id == 205).seed_kind is None)
    check("12. タイトル名だけでは除外しない（実案件 202 の名前は無関係）", 202 in tids)

    summary = rq.summarize([], excluded)
    check("13. skipped_dummy_or_test_data を集計", summary["dummy_or_test_skipped"] == 2)
    check("13b. skipped_no_research_seed を集計",
          summary["no_research_seed_skipped"] == 2)

    # 12b. タイトル・maker 名がダミーでも campaign_url があれば対象
    make_project(db, 207, title="Overdue Gadget")
    p2 = db.get(Project, 207)
    p2.maker_name = "OverdueCo"
    db.commit()
    make_outreach(db, 207)
    t2, _ = rq.collect_candidates(db, project_id=207, active_heavy=no_active)
    check("12b. テスト風のタイトル/メーカー名でも campaign_url があれば対象",
          [c.project_id for c in t2] == [207])
    db.close()


def test_dummy_no_hardcoded_ids() -> None:
    code = _code_only()
    check("11. project ID のハードコードがない",
          not re.search(r"\b(13[7-9]|14[0-3])\b", code))
    check("11b. タイトル名で判定しない",
          "Gadget" not in SCRIPT_SRC and "Editable" not in SCRIPT_SRC)
    check("11c. メーカー名で判定しない",
          "EditCo" not in SCRIPT_SRC and "SendCo" not in SCRIPT_SRC)
    check("11d. 既存ヘルパを再利用（判定を再実装しない）",
          "is_dummy_domain" in code and "business_url_reason" in code)
    check("11e. 予約ドメイン一覧を自前で持たない",
          "example.com" not in code and "example.org" not in code)


def test_dummy_json_output() -> None:
    reset_db()
    db = SessionLocal()
    make_project(db, 210, title="ダミー", source_url=None)
    make_outreach(db, 210)
    make_discovery(db, 210, email="secret-owner@example.com",
                   official_site="https://maker.example.com")
    make_project(db, 211, title="実案件")
    make_outreach(db, 211)
    db.close()

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = rq.main(["--json", "--limit", "99"])
    out = buf.getvalue()
    payload = json.loads(out)
    ex = {e["project_id"]: e for e in payload["excluded"]}

    check("14. JSON に除外理由が入る",
          ex[210]["excluded_reason"] == rq.R_DUMMY)
    check("14b. JSON に判定根拠が入る",
          "official_site:" in " ".join(ex[210]["dummy_signals"]))
    check("14c. JSON に exclusion_detail が入る",
          "reserved_domain_and_no_campaign_url" in ex[210]["exclusion_detail"])
    check("14d. JSON に campaign_url の有無が入る",
          ex[210]["has_campaign_url"] is False)
    check("14e. 対象には seed_kind が入る",
          payload["targets"][0]["seed_kind"] == "campaign_url")
    check("15. JSON にメールアドレスを出さない",
          "secret-owner@example.com" not in out and not re.search(r"[\w.]+@[\w.]+", out))
    check("15b. ドメイン全文もラベル化して出す",
          "email_domain:reserved" in json.dumps(ex[210], ensure_ascii=False))
    check("14f. 実案件は対象に残る", payload["target_project_ids"] == [211])
    check("14g. exit 0", code == rq.EXIT_OK)

    # 人が読む出力にもダミー区分が出る
    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        rq.main(["--limit", "99"])
    out2 = buf2.getvalue()
    check("14h. dry-run 表示にダミー除外の節がある",
          "ダミー/テストデータ除外" in out2)
    check("14i. 表示に project_id と reason が出る",
          "project_id=210" in out2 and "reserved_domain_and_no_campaign_url" in out2)
    check("15c. 表示にメールアドレスを出さない", "secret-owner@example.com" not in out2)


def test_dummy_no_side_effects() -> None:
    reset_db()
    db = SessionLocal()
    make_project(db, 220, title="ダミー", source_url=None)
    make_outreach(db, 220)
    make_discovery(db, 220, official_site="https://maker.example.com")
    make_project(db, 221, title="実案件")
    make_outreach(db, 221)
    before = counts(db)
    db.close()

    fake = FakeCreateJob()
    orig = ci.create_job
    ci.create_job = fake
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rq.main(["--limit", "99"])
    finally:
        ci.create_job = orig

    db = SessionLocal()
    after = counts(db)
    db.close()
    check("16. dry-run で DB 書き込みなし", before == after)
    check("17. create_job 呼び出しなし", fake.calls == [])

    body = _code_only()
    check("18. 外部 HTTP なし",
          not re.search(r"\b(requests|httpx|urllib|aiohttp)\b", body))
    check("19. Brave API なし", "brave" not in body.lower())
    check("20. Playwright なし", "playwright" not in body.lower())
    check("21. Gmail API なし", "gmail" not in body.lower())
    check("22. LQE run なし",
          "lead_qualification" not in body.lower() and not re.search(r"\brun\(", body))
    check("23. contact_intel_eval に触れない",
          "contact_intel_eval" not in SCRIPT_SRC)
    check("23b. 再利用するのは判定ヘルパのみ（探索サービスを呼ばない）",
          "run_discovery" not in body and "run_contact_discovery" not in body)


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
    print("--- ダミー/テストデータ除外 ---")
    test_dummy_signals_pure()
    test_research_seed_pure()
    test_dummy_exclusion_in_collect()
    test_dummy_no_hardcoded_ids()
    test_dummy_json_output()
    test_dummy_no_side_effects()
    print(f"\n{_passed} passed / {_failed} failed")
    return _failed


if __name__ == "__main__":
    sys.exit(main())
