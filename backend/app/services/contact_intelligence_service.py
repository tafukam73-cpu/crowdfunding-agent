"""Contact Intelligence：重い探索ジョブのキューイングと実行ロジック。

重い探索（full / web_research / document_reader / search_agent / ai_research /
contact_discovery(_v2) 等）を API（uvicorn）プロセス内のスレッドで実行すると、
外部取得のハングや CPU スピンがイベントループを飢餓状態にして /health すら無応答に
なる。そこで実行を独立した専用ワーカープロセス（cfagent-ci-worker）へ分離した。

- API 側（create_job）: バリデーション → 重複確認 → queued 行を作って即返すだけ。
  **API プロセスからスレッド・サブプロセスは一切起動しない。**
- ワーカー側（app.workers.contact_intelligence_worker）: queued 行を原子的に claim し、
  ジョブごとにサブプロセス（app.workers.run_single_job）で実行する。ハードタイムアウト／
  中断時はプロセスツリーごと kill するので、スレッドを「見捨てる」ことはしない。
- 本モジュールの execute_job() が実際のフェーズ実行本体で、サブプロセス内で呼ばれる。
- cancel: DB の cancel_requested フラグで行う（プロセス跨ぎ）。queued は即 cancelled、
  running はワーカーが検知して実行プロセスを終了させる。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import worker_session
from app.models.contact_intelligence_job import (
    CIJobStatus,
    CIJobType,
    ContactIntelligenceJob,
)
from app.models.project import Project


@dataclass(frozen=True)
class ProjectContext:
    """フェーズ実行の入口で必要になる案件のスカラー値だけを持つ DTO。

    外部処理（HTTP/Playwright/Claude）中に ORM や DB セッションを保持しないための
    受け渡し用。ORM の Project を外部処理へ直接引き回さない起点になる。
    """

    project_id: int
    job_type: str
    title: str | None = None
    source_site: str | None = None

    @classmethod
    def of(cls, job: "ContactIntelligenceJob", project: Project) -> "ProjectContext":
        return cls(
            project_id=project.id,
            job_type=job.job_type,
            title=getattr(project, "title", None),
            source_site=getattr(project, "source_site", None),
        )
from app.services import (
    contact_discovery_service,
    contact_discovery_v2_service,
    document_reader_service,
    recursive_crawl_service,
    search_agent_service,
    web_research_service,
)

logger = logging.getLogger("contact_intelligence")

CACHE_TTL_HOURS = 24

# ブラウザ（Playwright/Chromium）を伴う重い探索ジョブ。これらは同一 project で
# 並列起動すると Chromium プロセスが増殖して CPU/GIL を飽和させ、実行プロセスを
# 過負荷にする。full は web/recursive/doc/search を内包するため、full と各子ジョブ、
# および子ジョブ同士も同一 project では相互排他とし、active は project あたり最大 1 本。
# claim（ワーカー）と create_job（API）双方でこの不変条件を守る。
_HEAVY_JOB_TYPES = {
    CIJobType.full_contact_intelligence.value,
    CIJobType.web_research.value,
    CIJobType.recursive_crawl.value,
    CIJobType.document_reader.value,
    CIJobType.search_agent.value,
    CIJobType.contact_discovery.value,
    CIJobType.contact_discovery_v2.value,
    CIJobType.ai_research.value,
    CIJobType.zeczec_enrichment.value,
}

# メール探索（連絡先探索）に該当するジョブ。開始前に日本クラファン適性ゲートを
# サーバー側で再判定する。日本販売状況チェックや営業メール生成など、探索を伴わない
# ジョブはゲートの対象外（既存の運用を壊さない）。
_CONTACT_SEARCH_JOB_TYPES = {
    CIJobType.full_contact_intelligence.value,
    CIJobType.web_research.value,
    CIJobType.recursive_crawl.value,
    CIJobType.document_reader.value,
    CIJobType.search_agent.value,
    CIJobType.contact_discovery.value,
    CIJobType.contact_discovery_v2.value,
    CIJobType.ai_research.value,
}

_TERMINAL_STATUSES = (
    CIJobStatus.completed.value,
    CIJobStatus.failed.value,
    CIJobStatus.cancelled.value,
    CIJobStatus.timed_out.value,
)

# 1 ジョブのウォールクロック上限（秒）。ワーカーがこの時間を超えた実行プロセスを
# ツリーごと kill する。0 で無効。
_HARD_TIMEOUT_SECONDS = max(
    0, int(getattr(settings, "ci_job_hard_timeout_minutes", 20)) * 60
)
# ワーカー生存とみなす heartbeat の鮮度（秒）。これより古い running はワーカー
# 死亡とみなして stale 回収する。ハードタイムアウトより十分短く、かつワーカーの
# heartbeat 更新間隔より十分長くする。
_HEARTBEAT_STALE_SECONDS = max(
    60, int(getattr(settings, "ci_heartbeat_stale_seconds", 90))
)


class _JobCancelled(BaseException):
    """フェーズ実行中に中断が要求されたことを示す内部シグナル。

    各サービス（run_web_research 等）の `except Exception` に握り潰されず、ジョブ
    ランナーまで伝播させるため BaseException を継承する（中断を即時に反映する）。
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log(job: ContactIntelligenceJob, message: str) -> None:
    logs = list(job.logs_json or [])
    logs.append({"ts": _now().isoformat(), "message": message})
    job.logs_json = logs[-100:]  # 上限 100 行


def _find_cached(
    db: Session, project_id: int, job_type: str
) -> ContactIntelligenceJob | None:
    """24 時間以内に completed した同種ジョブを返す（キャッシュ判定）。"""
    since = _now() - timedelta(hours=CACHE_TTL_HOURS)
    stmt = (
        select(ContactIntelligenceJob)
        .where(
            ContactIntelligenceJob.project_id == project_id,
            ContactIntelligenceJob.job_type == job_type,
            ContactIntelligenceJob.status == CIJobStatus.completed.value,
            ContactIntelligenceJob.completed_at >= since,
        )
        .order_by(desc(ContactIntelligenceJob.completed_at))
        .limit(1)
    )
    return db.scalar(stmt)


def get_job(db: Session, job_id: int) -> ContactIntelligenceJob | None:
    return db.get(ContactIntelligenceJob, job_id)


def recover_stale_jobs(db: Session, project_id: int | None = None) -> int:
    """heartbeat が途絶えた running ジョブを timed_out に回収する（ワーカー死亡回収）。

    専用ワーカーは実行中サブプロセスがある間 heartbeat_at を更新し続ける。ワーカー／
    サブプロセスが異常終了して heartbeat が古くなった（または最初から無い）running を
    「実行主体が消えた孤児」とみなして回収する。再起動を待たず、重複抑止が永久ロック
    するのを防ぐ。読み取り専用パス（ポーリング GET）からは呼ばない。

    Returns: 回収した件数。
    """
    cutoff = _now() - timedelta(seconds=_HEARTBEAT_STALE_SECONDS)
    stmt = select(ContactIntelligenceJob).where(
        ContactIntelligenceJob.status == CIJobStatus.running.value,
        func.coalesce(
            ContactIntelligenceJob.heartbeat_at,
            ContactIntelligenceJob.started_at,
            ContactIntelligenceJob.created_at,
        )
        < cutoff,
    )
    if project_id is not None:
        stmt = stmt.where(ContactIntelligenceJob.project_id == project_id)
    rows = list(db.scalars(stmt))
    for job in rows:
        job.status = CIJobStatus.timed_out.value
        job.error = (
            "実行プロセスの応答が途絶えたため回収されました"
            "（ワーカー異常終了の可能性。再実行してください）"
        )
        job.current_step = "回収（heartbeat 途絶）"
        job.completed_at = _now()
    if rows:
        db.commit()
        logger.warning("recovered %d stale CI jobs (heartbeat lost)", len(rows))
    return len(rows)


def claim_next_job(db: Session, worker_id: str) -> tuple[int, str] | None:
    """queued ジョブを 1 件、原子的に claim して running にする。

    - 古い順に、`FOR UPDATE SKIP LOCKED` で行ロックしながら候補を取り、同一 project に
      running の重い探索が無いものを選ぶ（heavy は project あたり 1 本の不変条件）。
    - 選んだ行に status=running / worker_id / 一意 execution_token / started_at /
      heartbeat_at を書いて commit する。複数ワーカーでも二重実行しない。

    Returns: (job_id, execution_token) または None（対象なし）。
    """
    # PostgreSQL では FOR UPDATE SKIP LOCKED で行ロック（複数ワーカーで二重 claim 防止）。
    # SQLite（テスト）では with_for_update は無視されるが、単一プロセスなので問題ない。
    stmt = (
        select(ContactIntelligenceJob)
        .where(ContactIntelligenceJob.status == CIJobStatus.queued.value)
        .order_by(ContactIntelligenceJob.id)
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    locked = list(db.scalars(stmt))
    for job in locked:
        if job.job_type in _HEAVY_JOB_TYPES and _project_has_running_heavy(
            db, job.project_id
        ):
            continue  # 同一 project の重い探索が実行中なのでこのジョブはまだ回さない
        token = uuid.uuid4().hex
        now = _now()
        job.status = CIJobStatus.running.value
        job.worker_id = worker_id
        job.execution_token = token
        job.started_at = now
        job.heartbeat_at = now
        job.progress = max(1, job.progress or 1)
        job.cancel_requested = False
        db.commit()
        return job.id, token
    db.rollback()  # ロックを解放（何も claim しなかった）
    return None


def _project_has_running_heavy(db: Session, project_id: int) -> bool:
    stmt = (
        select(ContactIntelligenceJob.id)
        .where(
            ContactIntelligenceJob.project_id == project_id,
            ContactIntelligenceJob.status == CIJobStatus.running.value,
            ContactIntelligenceJob.job_type.in_(list(_HEAVY_JOB_TYPES)),
        )
        .limit(1)
    )
    return db.scalar(stmt) is not None


def heartbeat(db: Session, job_id: int) -> bool:
    """ワーカーが実行中ジョブの生存を更新する。running でなければ False（停止合図）。"""
    job = db.get(ContactIntelligenceJob, job_id)
    if job is None or job.status != CIJobStatus.running.value:
        return False
    job.heartbeat_at = _now()
    db.commit()
    return True


def is_cancel_requested(db: Session, job_id: int) -> bool:
    """ワーカーが running ジョブの中断要求を確認する（true なら実行プロセスを kill）。"""
    return _is_cancelled(db, job_id)


def finalize_terminated(
    db: Session, job_id: int, status: str, message: str
) -> bool:
    """実行プロセスを kill／異常終了させた後に、まだ running の行を終端状態に確定する。

    サブプロセスが自分で終端状態を書けたケース（正常完了・自己 failed）は running では
    ないので上書きしない。Returns: 実際に確定したら True。
    """
    job = db.get(ContactIntelligenceJob, job_id)
    if job is None or job.status != CIJobStatus.running.value:
        return False
    job.status = status
    job.error = message
    job.current_step = {
        CIJobStatus.timed_out.value: "タイムアウト（プロセス終了）",
        CIJobStatus.cancelled.value: "中断されました",
        CIJobStatus.failed.value: "失敗",
    }.get(status, status)
    job.completed_at = _now()
    _log(job, message)
    db.commit()
    return True


def find_active(
    db: Session, project_id: int, job_type: str
) -> ContactIntelligenceJob | None:
    """進行中（queued/running）の同種ジョブを返す（重複ジョブ作成の抑止に使う）。"""
    stmt = (
        select(ContactIntelligenceJob)
        .where(
            ContactIntelligenceJob.project_id == project_id,
            ContactIntelligenceJob.job_type == job_type,
            ContactIntelligenceJob.status.in_(
                [CIJobStatus.queued.value, CIJobStatus.running.value]
            ),
        )
        .order_by(desc(ContactIntelligenceJob.id))
        .limit(1)
    )
    return db.scalar(stmt)


def find_active_heavy(
    db: Session, project_id: int
) -> ContactIntelligenceJob | None:
    """同一 project で進行中の重い探索ジョブ（ブラウザ系）を 1 本返す。

    full と各子ジョブ・子ジョブ同士の並列起動を抑止するために使う。
    """
    stmt = (
        select(ContactIntelligenceJob)
        .where(
            ContactIntelligenceJob.project_id == project_id,
            ContactIntelligenceJob.job_type.in_(list(_HEAVY_JOB_TYPES)),
            ContactIntelligenceJob.status.in_(
                [CIJobStatus.queued.value, CIJobStatus.running.value]
            ),
        )
        .order_by(desc(ContactIntelligenceJob.id))
        .limit(1)
    )
    return db.scalar(stmt)


def get_latest(
    db: Session, project_id: int, job_type: str | None = None
) -> ContactIntelligenceJob | None:
    stmt = select(ContactIntelligenceJob).where(
        ContactIntelligenceJob.project_id == project_id
    )
    if job_type:
        stmt = stmt.where(ContactIntelligenceJob.job_type == job_type)
    stmt = stmt.order_by(desc(ContactIntelligenceJob.id)).limit(1)
    return db.scalar(stmt)


def create_job(
    db: Session,
    project: Project,
    job_type: str,
    *,
    force: bool = False,
    runner=None,
    override_reason: str | None = None,
) -> tuple[ContactIntelligenceJob, bool]:
    """ジョブを作成（or キャッシュ再利用）。(job, from_cache) を返す。

    runner を渡すとスレッド起動の代わりに同期実行する（テスト用）。

    メール探索系（_CONTACT_SEARCH_JOB_TYPES）は **サーバー側で** 日本クラファン適性
    ゲートを再判定する。フロントのボタン非表示だけに頼らない。不合格の場合は
    ``GateBlocked`` を送出し、override_reason（管理者の手動実行理由）が与えられた
    ときのみ理由を記録して実行する。
    """
    if job_type not in {t.value for t in CIJobType}:
        raise ValueError(f"未知の job_type: {job_type}")

    gate_override: str | None = None
    if job_type in _CONTACT_SEARCH_JOB_TYPES:
        from app.services import contact_search_gate

        gate = contact_search_gate.require_eligible(
            db, project, override_reason=override_reason
        )
        if gate.get("override"):
            gate_override = gate.get("override_reason")

    # heartbeat が途絶えた running（ワーカー死亡の孤児）を回収してから重複判定する。
    # これで dead worker のジョブが重複抑止を永久ロックするのを防ぐ（書き込みパスのみ）。
    recover_stale_jobs(db, project.id)

    # 重複・並列増殖の抑止（force でも無視しない。force は 24h キャッシュの無視のみ）。
    # 同一 project の重い探索は active を最大 1 本に絞る（full と子ジョブ、子ジョブ同士も
    # 相互排他）。既に進行中なら新規行を作らずその active ジョブを返す。
    if job_type in _HEAVY_JOB_TYPES:
        active = find_active_heavy(db, project.id)
        if active is not None:
            return active, False
    else:
        active = find_active(db, project.id, job_type)
        if active is not None:
            return active, False

    if not force:
        cached = _find_cached(db, project.id, job_type)
        if cached is not None:
            return cached, True

    logs = [{"ts": _now().isoformat(), "message": "ジョブを受け付けました"}]
    if job_type in _CONTACT_SEARCH_JOB_TYPES:
        # 探索ジョブへ元の商品ページ URL を引き継ぐ（どの商品を調べたか後から辿れる）。
        from app.services import campaign_url as campaign_url_mod

        campaign = campaign_url_mod.campaign_url_of(project)
        logs.append(
            {
                "ts": _now().isoformat(),
                "message": (
                    f"対象商品: {project.title} / 商品ページ: "
                    + (campaign or "未確認")
                ),
            }
        )
    if gate_override:
        logs.append(
            {
                "ts": _now().isoformat(),
                "message": f"適性ゲート不合格のまま手動実行（理由: {gate_override}）",
            }
        )
    job = ContactIntelligenceJob(
        project_id=project.id,
        job_type=job_type,
        status=CIJobStatus.queued.value,
        progress=0,
        gate_override_reason=gate_override,
        logs_json=logs,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # **API プロセスからは実処理を一切起動しない**（スレッド・サブプロセス禁止）。
    # queued 行だけ作り、専用ワーカー（cfagent-ci-worker）が claim して実行する。
    # runner はテスト用の同期実行フック（本番では None）。
    if runner is not None:
        runner(job.id)
    return job, False


def queue_reassessment(
    db: Session, project: Project, *, runner=None
) -> ContactIntelligenceJob:
    """Wadiz 取り込み後の営業適性再評価ジョブを重複なく作成する。

    同一 project・同一 job_type の queued/running が既にあればそれを返す（重複作成禁止）。
    confirm から呼ばれ、レスポンス後に非同期で実行される（confirm を同期で重くしない）。
    runner を渡すと同期実行する（テスト用）。
    """
    active = find_active(
        db, project.id, CIJobType.wadiz_contact_reassessment.value
    )
    if active is not None:
        return active
    job, _from_cache = create_job(
        db,
        project,
        CIJobType.wadiz_contact_reassessment.value,
        force=True,  # 24h キャッシュを無視して常に最新の連絡先で再評価
        runner=runner,
    )
    return job


def request_cancel(db: Session, job_id: int) -> ContactIntelligenceJob | None:
    """ジョブに中断を要求する（プロセス跨ぎ＝DB フラグで行う）。

    - queued: まだ誰も実行していないので即 cancelled にする。
    - running: cancel_requested=true を立て、ワーカーが検知して実行プロセスを終了させる。
    """
    job = db.get(ContactIntelligenceJob, job_id)
    if job is None:
        return None
    if job.status == CIJobStatus.queued.value:
        job.status = CIJobStatus.cancelled.value
        job.cancel_requested = True
        job.current_step = "中断されました"
        job.completed_at = _now()
        _log(job, "実行前に中断しました")
        db.commit()
        db.refresh(job)
    elif job.status == CIJobStatus.running.value:
        job.cancel_requested = True
        _log(job, "中断が要求されました（実行プロセスを停止します）")
        db.commit()
        db.refresh(job)
    return job


def _is_cancelled(db: Session, job_id: int) -> bool:
    """DB の cancel_requested を確認する（サブプロセス内での協調的中断判定）。"""
    val = db.scalar(
        select(ContactIntelligenceJob.cancel_requested).where(
            ContactIntelligenceJob.id == job_id
        )
    )
    return bool(val)


# ---------------- ジョブ実行（専用ワーカーのサブプロセス内） ----------------
# 進捗・中断・ステップ更新はすべて **短命セッション**（worker_session）で行う。
# フェーズ本体（外部処理）実行中は DB セッション／トランザクションを保持しない。


def _make_progress_cb(job_id: int, base: int, span: int):
    """フェーズ内の進捗コールバックを作る。呼ばれるたびに **新しい短命セッション** で
    current_step / progress / heartbeat を更新して即 commit・close する。フェーズが
    保持する長寿命セッションには一切触れない（外部処理中の接続保持を避ける）。"""

    def cb(message: str, pct: float | None = None) -> None:
        with worker_session() as db:
            job = db.get(ContactIntelligenceJob, job_id)
            if job is None:
                return
            # 中断要求があればフェーズを速やかに打ち切る（URL/クエリ境界で確認）。
            if job.cancel_requested:
                raise _JobCancelled()
            job.current_step = str(message)[:120]
            _log(job, message)
            if pct is not None:
                p = max(0.0, min(1.0, pct))
                job.progress = min(99, int(base + span * p))
            # 進捗があるうちは heartbeat も更新（ワーカー監視と二重で生存を示す）。
            job.heartbeat_at = _now()
            db.commit()

    return cb


def _make_cancel_checker(job_id: int):
    """中断確認用のクロージャ。呼ぶたびに短命セッションで cancel_requested を読む。"""

    def check() -> bool:
        with worker_session() as db:
            return _is_cancelled(db, job_id)

    return check


def _set_step(job_id: int, step: str, progress: int | None = None) -> None:
    """フェーズ境界のステップ/進捗更新（短命セッション）。"""
    with worker_session() as db:
        job = db.get(ContactIntelligenceJob, job_id)
        if job is None:
            return
        job.current_step = step[:120]
        if progress is not None:
            job.progress = progress
        _log(job, step)
        job.heartbeat_at = _now()
        db.commit()


# 各フェーズ本体（外部処理）。**引数は project_id と cb だけ**。DB セッションはフェーズ
# 内で短命に開閉し、外部処理中は保持しない（各 run_* サービス側で read→commit→external
# →save の順に接続を解放する）。ここでは project を短命セッションでロードして渡すが、
# run_* は expire_on_commit=False 前提で外部処理前にトランザクションを解放する。
def _run_web(project_id: int, cb=None) -> None:
    with worker_session() as db:
        project = db.get(Project, project_id)
        web_research_service.run_web_research(db, project, progress_cb=cb)


def _run_doc(project_id: int, cb=None) -> None:
    with worker_session() as db:
        project = db.get(Project, project_id)
        document_reader_service.run_document_reader(db, project, progress_cb=cb)


def _run_agent(project_id: int, cb=None) -> None:
    with worker_session() as db:
        project = db.get(Project, project_id)
        search_agent_service.run_search_agent(db, project, progress_cb=cb)


def _run_recursive(project_id: int, cb=None) -> None:
    with worker_session() as db:
        project = db.get(Project, project_id)
        recursive_crawl_service.run_recursive_crawl(db, project, progress_cb=cb)


def _run_auto(project_id: int, cb=None) -> None:
    with worker_session() as db:
        project = db.get(Project, project_id)
        contact_discovery_service.run_discovery(db, project)


def _run_v2(project_id: int, cb=None) -> None:
    with worker_session() as db:
        project = db.get(Project, project_id)
        contact_discovery_v2_service.run_contact_discovery_v2(
            db, project, progress_cb=cb
        )


def _run_ai(project_id: int, cb=None) -> None:
    with worker_session() as db:
        project = db.get(Project, project_id)
        contact_discovery_service.run_ai_research(db, project)


def _run_japan_sales_check(project_id: int, cb=None) -> None:
    from app.services import japan_sales_service, sales_assessment_service

    if cb:
        cb("日本販売状況をチェック中", 0.1)
    with worker_session() as db:
        project = db.get(Project, project_id)
        japan_sales_service.run_check(db, project)
    if cb:
        cb("営業適性を再計算中（独占販売可能性の確度を更新）", 0.8)
    with worker_session() as db:
        project = db.get(Project, project_id)
        sales_assessment_service.run_assessment(db, project)


def _run_outreach_generation(project_id: int, cb=None) -> None:
    from app.services import sales_outreach_service

    with worker_session() as db:
        project = db.get(Project, project_id)
        sales_outreach_service.run_generation(db, project, progress_cb=cb)


def _run_followup_generation(project_id: int, cb=None) -> None:
    from app.services import sales_outreach_service

    with worker_session() as db:
        project = db.get(Project, project_id)
        sales_outreach_service.run_followup_generation(db, project, progress_cb=cb)


def _run_wadiz_reassessment(project_id: int, cb=None) -> None:
    from app.services import sales_assessment_service

    if cb:
        cb("営業適性を再計算中（Wadiz 連絡先を反映）", 0.3)
    with worker_session() as db:
        project = db.get(Project, project_id)
        sales_assessment_service.run_assessment(db, project)
    if cb:
        cb("再評価が完了しました", 0.95)


def _run_zeczec_enrichment(project_id: int, cb=None) -> None:
    from app.services import zeczec_enrichment_service
    from app.services.search_providers import get_search_fn

    search = get_search_fn()
    try:
        with worker_session() as db:
            project = db.get(Project, project_id)
            zeczec_enrichment_service.enrich_project(
                db, project, search_fn=search, progress_cb=cb
        )
    finally:
        try:
            search.close()
        except Exception:  # noqa: BLE001
            pass


_SINGLE_PHASES = {
    CIJobType.web_research.value: ("Web Research", _run_web),
    CIJobType.document_reader.value: ("AI Document Reader", _run_doc),
    CIJobType.search_agent.value: ("AI Search Agent", _run_agent),
    CIJobType.recursive_crawl.value: ("公式サイト再帰クロール", _run_recursive),
    CIJobType.contact_discovery.value: ("自動抽出（公式サイト巡回）", _run_auto),
    CIJobType.contact_discovery_v2.value: ("Contact Discovery v2", _run_v2),
    CIJobType.ai_research.value: ("AI連絡先リサーチ", _run_ai),
    CIJobType.zeczec_enrichment.value: ("Zeczec 詳細補完", _run_zeczec_enrichment),
    CIJobType.japan_sales_check.value: ("日本販売状況チェック", _run_japan_sales_check),
    CIJobType.wadiz_contact_reassessment.value: (
        "Wadiz 取り込み後の営業適性再評価",
        _run_wadiz_reassessment,
    ),
    CIJobType.outreach_generation.value: (
        "営業メール生成（4 言語）",
        _run_outreach_generation,
    ),
    CIJobType.followup_generation.value: (
        "フォローアップメール生成",
        _run_followup_generation,
    ),
}


def _finalize(
    job_id: int, status: str, log_msg: str, *, step: str, error: str | None = None
) -> str:
    """終端状態を短命セッションで確定する。"""
    with worker_session() as db:
        job = db.get(ContactIntelligenceJob, job_id)
        if job is not None:
            job.status = status
            job.current_step = step
            if error is not None:
                job.error = error
            _log(job, log_msg)
            job.completed_at = _now()
            db.commit()
    return status


def execute_job(job_id: int, *, execution_token: str | None = None) -> str:
    """ジョブ本体（**専用ワーカーのサブプロセス内で呼ばれる**）。

    各ステージ（開始・各フェーズ・進捗・終端）を **短命セッション** で実行し、外部処理
    （HTTP/Playwright/Claude）中は DB セッション／トランザクションを一切保持しない。
    ジョブ全体を覆う 1 本のセッションは作らない。

    - execution_token を渡すと開始時に DB の execution_token と一致することを確認する
      （再 claim された stale 実行が結果を書き込むのを防ぐ）。
    - スレッド・セマフォは使わない。ハードタイムアウト／中断時はワーカーがこのプロセス
      ツリーごと kill する（見捨て無し）。
    Returns: 終了ステータス（completed/failed/cancelled/timed_out）。
    """
    # --- ステージ 1：所有権確認 + running 設定 + DTO 化（短命セッション）---
    with worker_session() as db:
        job = db.get(ContactIntelligenceJob, job_id)
        if job is None:
            return CIJobStatus.failed.value
        if execution_token is not None and job.execution_token != execution_token:
            logger.warning(
                "execute_job %s: token mismatch (job reclaimed); skipping", job_id
            )
            return job.status
        project = db.get(Project, job.project_id)
        if project is None:
            job.status = CIJobStatus.failed.value
            job.error = "案件が見つかりません"
            job.completed_at = _now()
            db.commit()
            return job.status
        job.status = CIJobStatus.running.value
        if job.started_at is None:
            job.started_at = _now()
        job.heartbeat_at = _now()
        job.progress = max(1, job.progress or 1)
        _log(job, "実行を開始しました")
        db.commit()
        ctx = ProjectContext.of(job, project)  # 以降 ORM/セッションを持ち回らない

    # --- ステージ 2：フェーズ実行（セッション非保持。各フェーズが短命セッションを開閉）---
    cancel_checker = _make_cancel_checker(job_id)
    try:
        if ctx.job_type == CIJobType.full_contact_intelligence.value:
            phases = _run_full(job_id, ctx, cancel_checker)
        else:
            name, fn = _SINGLE_PHASES[ctx.job_type]
            phases = [_run_phase(job_id, ctx.project_id, name, fn, 5, 90)]

        # --- ステージ 3：終端（短命セッション）---
        if cancel_checker():
            return _finalize(
                job_id, CIJobStatus.cancelled.value, "ジョブを中断しました",
                step="中断されました",
            )
        # 全フェーズ失敗なら failed（単一フェーズジョブの失敗もここに含まれる）。
        if phases and all(p.get("status") == "failed" for p in phases):
            err = next((p.get("error_message") for p in phases
                        if p.get("error_message")), "全フェーズが失敗しました")
            return _finalize(
                job_id, CIJobStatus.failed.value, f"失敗しました: {err}",
                step="失敗", error=err,
            )
        with worker_session() as db:
            job = db.get(ContactIntelligenceJob, job_id)
            if job is None:
                return CIJobStatus.failed.value
            counts = _signal_counts(db, ctx.project_id)
            outcome = _classify_outcome(counts, phases)
            result = _build_result(db, ctx.project_id)
            result["phases"] = phases
            result["outcome"] = outcome
            result["signal_counts"] = counts
            job.status = CIJobStatus.completed.value
            job.progress = 100
            job.current_step = _OUTCOME_LABEL.get(outcome, "完了")
            job.result_json = result
            _log(job, f"ジョブが完了しました（{outcome}）")
            job.completed_at = _now()
            db.commit()
            return job.status
    except _JobCancelled:
        return _finalize(
            job_id, CIJobStatus.cancelled.value, "ジョブを中断しました",
            step="中断されました",
        )
    except Exception as exc:  # noqa: BLE001  失敗は行に記録（プロセスは落とさない）
        logger.warning("contact intelligence job %s failed: %s", job_id, exc)
        return _finalize(
            job_id, CIJobStatus.failed.value, f"失敗しました: {exc}",
            step="失敗", error=str(exc)[:4000],
        )


def _run_full(
    job_id: int, ctx: ProjectContext, cancel_checker
) -> None:
    """full_contact_intelligence：Web調査 → 再帰クロール → Document Reader →
    Search Agent → 営業推奨連絡先ランキング更新 を **1 実行内で直列に** 実行する。
    **各フェーズ間でセッションを完全に切る**（1 本のセッションを使い回さない）。
    個別ジョブを並列起動することはしない。各フェーズ境界で中断を確認する。"""
    phases = [
        ("Web Research", _run_web, 0, 25),
        ("公式サイト再帰クロール", _run_recursive, 25, 20),
        ("AI Document Reader", _run_doc, 45, 25),
        ("AI Search Agent", _run_agent, 70, 24),
    ]
    records: list[dict] = []
    for name, fn, base, span in phases:
        if cancel_checker():
            break
        rec = _run_phase(job_id, ctx.project_id, name, fn, base, span)
        records.append(rec)
        # **1 フェーズの失敗で全体を止めない**：記録して次フェーズへ進む。

    if not cancel_checker():
        # 結果統合（ランキング更新）。ここまでで得た成果を集計する。
        _set_step(job_id, "営業推奨連絡先ランキングを更新中", 95)
        with worker_session() as db:
            row = contact_discovery_service.get_latest(db, ctx.project_id)
            ranked = (
                contact_discovery_service.build_sales_contacts(row) if row else []
            )
            job = db.get(ContactIntelligenceJob, job_id)
            if job is not None:
                _log(job, f"営業推奨連絡先ランキングを更新しました（{len(ranked)} 件）")
            db.commit()
    return records


def _run_phase(job_id, project_id, name, fn, base, span) -> dict:
    """1 フェーズを実行し、成否・所要時間・発見数・エラーを記録して返す。

    フェーズの失敗はここで捕捉し、上位（_run_full）には伝播させない
    （1 フェーズの失敗で全体を停止させないため）。中断（_JobCancelled）は伝播させる。
    """
    started = _now()
    _set_step(job_id, f"{name} 実行中", max(1, base))
    error_code = error_message = None
    status = "success"
    try:
        with worker_session() as db:
            before = _signal_counts(db, project_id)
        fn(project_id, _make_progress_cb(job_id, base, span))
        with worker_session() as db:
            after = _signal_counts(db, project_id)
    except _JobCancelled:
        raise
    except Exception as exc:  # noqa: BLE001  フェーズ失敗は記録して継続
        status = "failed"
        error_code = type(exc).__name__
        error_message = str(exc)[:500]
        logger.warning("CI phase '%s' failed (project=%s): %s", name, project_id, exc)
        with worker_session() as db:
            after = before = _signal_counts(db, project_id)
    finished = _now()
    delta = {k: max(0, after.get(k, 0) - before.get(k, 0)) for k in after}
    rec = {
        "phase": name,
        "status": status,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_s": round((finished - started).total_seconds(), 1),
        "emails_found": delta.get("emails", 0),
        "forms_found": delta.get("forms", 0),
        "socials_found": delta.get("socials", 0),
        "people_found": delta.get("people", 0),
        "totals": after,
        "error_code": error_code,
        "error_message": error_message,
        "retryable": status == "failed" and error_code in (
            "TimeoutException", "ConnectError", "ReadTimeout", "HTTPStatusError",
        ),
    }
    _set_step(job_id, f"{name} が完了しました" if status == "success"
              else f"{name} は失敗（継続）")
    return rec


# --- 成果（実連絡先／チャネル）の集計と完了アウトカム分類 ---
# 「completed だが成果 0 件」を正直に区別するための状態。status は completed のままだが、
# result_json.outcome に以下を入れ、UI はこれで表示を切り替える。
OUTCOME_WITH_CONTACTS = "completed_with_contacts"   # 実メール or 公開担当者あり
OUTCOME_WITH_CHANNELS = "completed_with_channels"   # フォーム/公式SNS/公式サイト等
OUTCOME_NO_CONTACTS = "completed_no_contacts"       # 何も見つからない
OUTCOME_PARTIAL = "partial_success"                 # 一部フェーズ失敗＋何か発見

_OUTCOME_LABEL = {
    OUTCOME_WITH_CONTACTS: "完了：実連絡先あり",
    OUTCOME_WITH_CHANNELS: "完了：連絡チャネルのみ",
    OUTCOME_NO_CONTACTS: "完了：成果なし",
    OUTCOME_PARTIAL: "完了：一部成功",
}


def _signal_counts(db: Session, project_id: int) -> dict:
    """保存済みの実成果シグナル数（メール/フォーム/SNS/公式サイト/担当者）。"""
    from app.models.contact_person import ContactPerson

    people = (
        db.query(ContactPerson)
        .filter(ContactPerson.project_id == project_id, ContactPerson.name.isnot(None))
        .count()
    )
    row = contact_discovery_service.get_latest(db, project_id)
    if row is None:
        return {"emails": 0, "forms": 0, "socials": 0, "official_sites": 0,
                "people": people}
    # メール：build_sales_contacts は営業可能メールのランキング（各要素に email を持つ）。
    ranked = contact_discovery_service.build_sales_contacts(row)
    emails = sum(1 for c in ranked if (c.get("email") or "").strip())
    # フォーム：ContactDiscovery 行の各レイヤーのフォームカラムから数える
    # （build_sales_contacts はフォームを返さないため、行から直接集計する）。
    forms = 0
    for f in ("primary_contact_form_url", "web_primary_contact_form_url",
              "ai_contact_form_url"):
        if getattr(row, f, None):
            forms += 1
    for f in ("discovered_forms", "web_discovered_forms", "v2_forms",
              "doc_reader_contact_forms", "search_agent_contact_forms",
              "recursive_forms"):
        v = getattr(row, f, None)
        if v:
            forms += len(v) if isinstance(v, (list, dict)) else 1
    official = 1 if contact_discovery_service.official_site_or_none(
        row.official_site_url
    ) or contact_discovery_service.official_site_or_none(
        getattr(row, "v2_official_site_url", None)
    ) else 0
    socials = 0
    for src in (row.web_discovered_socials, getattr(row, "v2_socials", None),
                getattr(row, "recursive_socials", None)):
        if src:
            socials += len(src) if isinstance(src, (list, dict)) else 1
    for f in ("instagram_url", "facebook_url", "linkedin_url", "youtube_url"):
        if getattr(row, f, None):
            socials += 1
    return {"emails": emails, "forms": forms, "socials": socials,
            "official_sites": official, "people": people}


def _classify_outcome(counts: dict, phases: list[dict]) -> str:
    """成果シグナルとフェーズ結果から完了アウトカムを決める。"""
    any_failed = any(p.get("status") == "failed" for p in phases)
    has_contacts = counts.get("emails", 0) > 0 or counts.get("people", 0) > 0
    # チャネル＝フォーム or 公式SNS。公式サイトは「そこから連絡先を探す起点」であって
    # それ自体は送信チャネルではない（要件の定義に合わせ channels には含めない）。
    has_channels = counts.get("forms", 0) > 0 or counts.get("socials", 0) > 0
    if any_failed and (has_contacts or has_channels):
        return OUTCOME_PARTIAL
    if has_contacts:
        return OUTCOME_WITH_CONTACTS
    if has_channels:
        return OUTCOME_WITH_CHANNELS
    return OUTCOME_NO_CONTACTS


def _build_result(db: Session, project_id: int) -> dict:
    """完了時の結果サマリ（UI 表示・キャッシュ用）。最新の探索結果から集計する。"""
    row = contact_discovery_service.get_latest(db, project_id)
    if row is None:
        return {"summary": "探索結果がありません。"}
    ranked = contact_discovery_service.build_sales_contacts(row)
    official = (
        contact_discovery_service.official_site_or_none(row.official_site_url)
        or contact_discovery_service.official_site_or_none(
            getattr(row, "search_agent_official_site_url", None)
        )
        or contact_discovery_service.official_site_or_none(
            getattr(row, "doc_reader_official_site_url", None)
        )
    )
    socials = {}
    for src in (
        row.web_discovered_socials,
        getattr(row, "doc_reader_socials", None),
        getattr(row, "search_agent_socials", None),
    ):
        for k, v in (src or {}).items():
            if v and not socials.get(k):
                socials[k] = v
    for src in (getattr(row, "recursive_socials", None),):
        for k, v in (src or {}).items():
            if v and not socials.get(k):
                socials[k] = v
    forms = list(row.web_discovered_forms or [])
    for f in getattr(row, "recursive_forms", None) or []:
        if f not in forms:
            forms.append(f)
    return {
        "official_site_url": official,
        "top_contact": ranked[0] if ranked else None,
        "sales_contacts_count": len(ranked),
        "socials": socials,
        "forms_count": len(forms),
        "recommended_channel": row.recommended_channel,
        # Contact Intelligence v3：再帰クロールのサマリ
        "recursive_crawl_enabled": bool(getattr(row, "recursive_crawl_enabled", False)),
        "recursive_crawled_count": len(getattr(row, "recursive_crawled_urls", None) or []),
        "recursive_pdf_count": len(getattr(row, "recursive_pdfs", None) or []),
        "recursive_has_mx": getattr(row, "recursive_has_mx", None),
        "recursive_mx_provider": getattr(row, "recursive_mx_provider", None),
        "recursive_failure_reasons": getattr(row, "recursive_failure_reasons", None) or [],
        "recursive_summary": getattr(row, "recursive_summary", None),
    }
