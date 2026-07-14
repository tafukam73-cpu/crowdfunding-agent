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
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import SessionLocal
from app.models.contact_intelligence_job import (
    CIJobStatus,
    CIJobType,
    ContactIntelligenceJob,
)
from app.models.project import Project
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
) -> tuple[ContactIntelligenceJob, bool]:
    """ジョブを作成（or キャッシュ再利用）。(job, from_cache) を返す。

    runner を渡すとスレッド起動の代わりに同期実行する（テスト用）。
    """
    if job_type not in {t.value for t in CIJobType}:
        raise ValueError(f"未知の job_type: {job_type}")

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

    job = ContactIntelligenceJob(
        project_id=project.id,
        job_type=job_type,
        status=CIJobStatus.queued.value,
        progress=0,
        logs_json=[{"ts": _now().isoformat(), "message": "ジョブを受け付けました"}],
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


# ---------------- ジョブ実行（別スレッド／独自セッション） ----------------
def _phase_progress(
    db: Session, job: ContactIntelligenceJob, base: int, span: int
):
    """フェーズ内の進捗コールバックを作る。各 URL/ステップで current_step・log・
    progress を更新し、DB へ即 commit（flush）する（UI が固まって見えないように）。"""

    def cb(message: str, pct: float | None = None) -> None:
        # 中断要求があればフェーズを速やかに打ち切る（URL/クエリ境界で確認）。
        if _is_cancelled(db, job.id):
            raise _JobCancelled()
        try:
            job.current_step = str(message)[:120]
            _log(job, message)
            if pct is not None:
                p = max(0.0, min(1.0, pct))
                job.progress = min(99, int(base + span * p))
            job.updated_at = _now()
            db.commit()
        except _JobCancelled:
            raise
        except Exception:  # noqa: BLE001  進捗更新失敗で本体は止めない
            db.rollback()

    return cb


def _run_web(db, project, cb=None) -> None:
    web_research_service.run_web_research(db, project, progress_cb=cb)


def _run_doc(db, project, cb=None) -> None:
    document_reader_service.run_document_reader(db, project, progress_cb=cb)


def _run_agent(db, project, cb=None) -> None:
    search_agent_service.run_search_agent(db, project, progress_cb=cb)


def _run_recursive(db, project, cb=None) -> None:
    recursive_crawl_service.run_recursive_crawl(db, project, progress_cb=cb)


def _run_auto(db, project, cb=None) -> None:
    # 自動抽出（公式サイト再帰クロールを含む重い探索）。run_discovery は progress_cb を
    # 取らないため cb は使わない（ジョブ境界の進捗のみ）。
    contact_discovery_service.run_discovery(db, project)


def _run_v2(db, project, cb=None) -> None:
    contact_discovery_v2_service.run_contact_discovery_v2(db, project, progress_cb=cb)


def _run_ai(db, project, cb=None) -> None:
    # AI連絡先リサーチ。run_ai_research は progress_cb を取らない。
    contact_discovery_service.run_ai_research(db, project)


def _run_japan_sales_check(db, project, cb=None) -> None:
    # 日本販売状況チェックを実行し、完了後に営業適性アセスメントを再計算する。
    from app.services import japan_sales_service, sales_assessment_service

    if cb:
        cb("日本販売状況をチェック中", 0.1)
    japan_sales_service.run_check(db, project)
    if cb:
        cb("営業適性を再計算中（独占販売可能性の確度を更新）", 0.8)
    # 日本販売チェックの結果を反映して再評価（新しい行として保存＝履歴保持）。
    sales_assessment_service.run_assessment(db, project)


def _run_outreach_generation(db, project, cb=None) -> None:
    # 営業実行パイプライン：4 言語の営業メールを生成し sales_outreach に保存、
    # CRM（sales_opportunities）へ反映する。外部 Claude 呼び出しはここ（背景）で行う。
    from app.services import sales_outreach_service

    sales_outreach_service.run_generation(db, project, progress_cb=cb)


def _run_followup_generation(db, project, cb=None) -> None:
    # 送信後フォローアップメールを生成し、sales_outreach の下書きを差し替える。
    # 決定的（ルールベース）だが既存の初回生成と同じく背景ジョブ化する。
    from app.services import sales_outreach_service

    sales_outreach_service.run_followup_generation(db, project, progress_cb=cb)


def _run_wadiz_reassessment(db, project, cb=None) -> None:
    # Wadiz 取り込み後の営業適性再評価。ルールベース・外部HTTPなし・軽量。
    # confirm のレスポンス後に非同期で実行し、v1/v2 Sales Copilot に反映させる。
    from app.services import sales_assessment_service

    if cb:
        cb("営業適性を再計算中（Wadiz 連絡先を反映）", 0.3)
    sales_assessment_service.run_assessment(db, project)
    if cb:
        cb("再評価が完了しました", 0.95)


def _run_zeczec_enrichment(db, project, cb=None) -> None:
    # Zeczec 詳細補完（Playwright で詳細ページを取得しメーカー名/カテゴリ/説明/公式
    # サイト候補を非破壊で書き戻す）。公式サイト候補の検索補完も行う。
    from app.services import zeczec_enrichment_service
    from app.services.search_providers import get_search_fn

    search = get_search_fn()
    try:
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


def execute_job(job_id: int, *, execution_token: str | None = None) -> str:
    """ジョブ本体（**専用ワーカーのサブプロセス内で呼ばれる**）。独自セッションで実行し、
    行を随時更新して、最終ステータス文字列を返す。

    - スレッド・セマフォは使わない（1 プロセス = 1 ジョブ）。ハードタイムアウト／中断時は
      ワーカーがこのプロセスツリーごと kill するため、「見捨てる」処理は無い。
    - execution_token を渡すと、開始時に DB の execution_token と一致することを確認する
      （再 claim された stale 実行が結果を書き込むのを防ぐ）。
    Returns: 終了ステータス（completed/failed/cancelled）。
    """
    db = SessionLocal()
    try:
        job = db.get(ContactIntelligenceJob, job_id)
        if job is None:
            return CIJobStatus.failed.value
        # 所有権確認：自分がこの claim の実行主体でなければ何もしない。
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

        # ワーカーの claim で running/started_at は設定済みだが、テスト等の直接呼び出しにも
        # 対応できるよう冪等に設定する。
        job.status = CIJobStatus.running.value
        if job.started_at is None:
            job.started_at = _now()
        job.heartbeat_at = _now()
        job.progress = max(1, job.progress or 1)
        _log(job, "実行を開始しました")
        db.commit()

        if job.job_type == CIJobType.full_contact_intelligence.value:
            _run_full(db, job, project)
        else:
            name, fn = _SINGLE_PHASES[job.job_type]
            job.current_step = f"{name} 実行中"
            job.progress = 5
            _log(job, f"{name} を実行します")
            db.commit()
            fn(db, project, _phase_progress(db, job, base=5, span=90))
            job.progress = 95
            _log(job, f"{name} が完了しました")
            db.commit()

        if _is_cancelled(db, job_id):
            job.status = CIJobStatus.cancelled.value
            job.current_step = "中断されました"
            _log(job, "ジョブを中断しました")
        else:
            job.status = CIJobStatus.completed.value
            job.progress = 100
            job.current_step = "完了"
            job.result_json = _build_result(db, project)
            _log(job, "ジョブが完了しました")
        job.completed_at = _now()
        db.commit()
        return job.status
    except _JobCancelled:
        db.rollback()
        try:
            job = db.get(ContactIntelligenceJob, job_id)
            if job is not None:
                job.status = CIJobStatus.cancelled.value
                job.current_step = "中断されました"
                _log(job, "ジョブを中断しました")
                job.completed_at = _now()
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        return CIJobStatus.cancelled.value
    except Exception as exc:  # noqa: BLE001  失敗は行に記録（プロセスは落とさない）
        logger.warning("contact intelligence job %s failed: %s", job_id, exc)
        try:
            job = db.get(ContactIntelligenceJob, job_id)
            if job is not None:
                job.status = CIJobStatus.failed.value
                job.error = str(exc)[:4000]
                job.current_step = "失敗"
                _log(job, f"失敗しました: {exc}")
                job.completed_at = _now()
                db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        return CIJobStatus.failed.value
    finally:
        db.close()


def _run_full(db: Session, job: ContactIntelligenceJob, project: Project) -> None:
    """full_contact_intelligence：Web調査 → 再帰クロール → Document Reader →
    Search Agent → 営業推奨連絡先ランキング更新 を **1 実行内で直列に** 実行する。
    個別ジョブを並列起動することはしない。各フェーズ境界で中断を確認する。"""
    # (name, fn, base_pct, span_pct)：各フェーズの進捗帯。
    # Web Research の直後に「公式サイト再帰クロール」を実行する（要件 11）。
    phases = [
        ("Web Research", _run_web, 0, 25),
        ("公式サイト再帰クロール", _run_recursive, 25, 20),
        ("AI Document Reader", _run_doc, 45, 25),
        ("AI Search Agent", _run_agent, 70, 24),
    ]
    for name, fn, base, span in phases:
        if _is_cancelled(db, job.id):
            return
        job.current_step = f"{name} 実行中"
        job.progress = max(1, base)
        _log(job, f"{name} を実行します")
        db.commit()
        fn(db, project, _phase_progress(db, job, base=base, span=span))
        _log(job, f"{name} が完了しました")
        db.commit()

    if _is_cancelled(db, job.id):
        return
    # 営業推奨連絡先ランキング更新（sales_contacts は都度算出のため保存対象なし。
    # ここでは最新行から集計してログ・結果に反映する）。
    job.current_step = "営業推奨連絡先ランキングを更新中"
    job.progress = 95
    db.commit()
    row = contact_discovery_service.get_latest(db, project.id)
    ranked = contact_discovery_service.build_sales_contacts(row) if row else []
    _log(job, f"営業推奨連絡先ランキングを更新しました（{len(ranked)} 件）")
    db.commit()


def _build_result(db: Session, project: Project) -> dict:
    """完了時の結果サマリ（UI 表示・キャッシュ用）。最新の探索結果から集計する。"""
    row = contact_discovery_service.get_latest(db, project.id)
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
