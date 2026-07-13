"""Contact Intelligence v2：重い探索の非同期ジョブ化。

AI Web調査 / Document Reader / Search Agent / full を HTTP リクエスト内で完了させると
タイムアウトするため、別スレッドで実行し、進捗・ログ・結果を contact_intelligence_jobs
に保存する。UI はポーリングで進捗を取得する。

- create_job: 24 時間以内に同 project_id/job_type の completed があれば再利用（force で無視）。
  無ければ queued 行を作り、デーモンスレッドで実行を開始する。
- ジョブスレッドは独自 DB セッションを使う（リクエストのセッションと分離）。
- cancel: 進行中ジョブに中断を要求（各フェーズ境界で確認して cancelled にする）。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
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
# 進行中ジョブへの中断要求（同一プロセス内シグナル。単一 uvicorn ワーカー想定）
_cancel_requested: set[int] = set()

# 重い外部クロール/検索ジョブの同時実行を制限するセマフォ。無制限に並列起動すると
# CPU/DB コネクションを食い潰して画面表示（GET）が固まるため。スロット待ちの間は
# DB セッションを開かない（ジョブ行は queued のまま）。
_MAX_CONCURRENT_JOBS = max(1, int(getattr(settings, "ci_max_concurrent_jobs", 2)))
_job_semaphore = threading.BoundedSemaphore(_MAX_CONCURRENT_JOBS)


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


def recover_orphaned_jobs(db: Session) -> int:
    """起動時に残っている queued/running ジョブを failed に回収する。

    バックエンド再起動でデーモンスレッドが失われると、ジョブ行が queued/running の
    まま孤児化し、find_active が永久に重複作成を抑止してしまう。起動時に一度掃除する。
    Returns: 回収した件数。
    """
    stmt = select(ContactIntelligenceJob).where(
        ContactIntelligenceJob.status.in_(
            [CIJobStatus.queued.value, CIJobStatus.running.value]
        )
    )
    rows = list(db.scalars(stmt))
    for job in rows:
        job.status = CIJobStatus.failed.value
        job.error = "バックエンド再起動により中断されました（再実行してください）"
        job.current_step = "中断"
        job.completed_at = _now()
    if rows:
        db.commit()
        logger.info("recovered %d orphaned CI jobs on startup", len(rows))
    return len(rows)


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

    if runner is not None:
        runner(job.id)  # 同期（テスト）
    else:
        threading.Thread(target=_run_job, args=(job.id,), daemon=True).start()
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
    """進行中ジョブに中断を要求する。queued/running のみ受け付ける。"""
    job = db.get(ContactIntelligenceJob, job_id)
    if job is None:
        return None
    if job.status in (CIJobStatus.queued.value, CIJobStatus.running.value):
        _cancel_requested.add(job_id)
        _log(job, "中断が要求されました")
        db.commit()
        db.refresh(job)
    return job


def _is_cancelled(job_id: int) -> bool:
    return job_id in _cancel_requested


# ---------------- ジョブ実行（別スレッド／独自セッション） ----------------
def _phase_progress(
    db: Session, job: ContactIntelligenceJob, base: int, span: int
):
    """フェーズ内の進捗コールバックを作る。各 URL/ステップで current_step・log・
    progress を更新し、DB へ即 commit（flush）する（UI が固まって見えないように）。"""

    def cb(message: str, pct: float | None = None) -> None:
        # 中断要求があればフェーズを速やかに打ち切る（URL/クエリ境界で確認）。
        if _is_cancelled(job.id):
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


def _run_job(job_id: int) -> None:
    """ジョブ本体。重い並列ジョブを制限してから実行する。

    セマフォ待ちの間は DB セッションを開かない（コネクションを掴んだまま待たない）。
    スロットを取得できるまでジョブ行は queued のまま。
    """
    with _job_semaphore:
        _run_job_inner(job_id)


def _run_job_inner(job_id: int) -> None:
    """ジョブ本体。独自セッションで実行し、行を随時更新する。"""
    db = SessionLocal()
    try:
        job = db.get(ContactIntelligenceJob, job_id)
        if job is None:
            return
        project = db.get(Project, job.project_id)
        if project is None:
            job.status = CIJobStatus.failed.value
            job.error = "案件が見つかりません"
            job.completed_at = _now()
            db.commit()
            return

        job.status = CIJobStatus.running.value
        job.started_at = _now()
        job.progress = 1
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

        if _is_cancelled(job_id):
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
    except Exception as exc:  # noqa: BLE001  失敗は行に記録（アプリは落とさない）
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
    finally:
        _cancel_requested.discard(job_id)
        db.close()


def _run_full(db: Session, job: ContactIntelligenceJob, project: Project) -> None:
    """full_contact_intelligence：Web調査 → Document Reader → Search Agent →
    営業推奨連絡先ランキング更新 を順に実行する。各フェーズ境界で中断を確認する。"""
    # (name, fn, base_pct, span_pct)：各フェーズの進捗帯。
    # Web Research の直後に「公式サイト再帰クロール」を実行する（要件 11）。
    phases = [
        ("Web Research", _run_web, 0, 25),
        ("公式サイト再帰クロール", _run_recursive, 25, 20),
        ("AI Document Reader", _run_doc, 45, 25),
        ("AI Search Agent", _run_agent, 70, 24),
    ]
    for name, fn, base, span in phases:
        if _is_cancelled(job.id):
            return
        job.current_step = f"{name} 実行中"
        job.progress = max(1, base)
        _log(job, f"{name} を実行します")
        db.commit()
        fn(db, project, _phase_progress(db, job, base=base, span=span))
        _log(job, f"{name} が完了しました")
        db.commit()

    if _is_cancelled(job.id):
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
