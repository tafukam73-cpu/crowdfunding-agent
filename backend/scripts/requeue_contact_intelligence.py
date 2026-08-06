"""Gmail 下書き候補・送信履歴のある案件へ Contact Intelligence v2 を再投入する管理コマンド。

**このスクリプト自身は探索をしません。** 既存ジョブ基盤（`contact_intelligence_service`）へ
`queued` 行を作るだけで、外部 HTTP（Playwright / Brave）は **ワーカー側**（`cfagent-ci-worker`）
で発生します。Chromium もここでは起動しません。

安全既定
--------
- **既定は dry-run。** `--execute` が無ければジョブを 1 件も作りません。
- `--execute` は `--reason` と `--confirm-count` の同時指定が必須。
  実対象件数と `--confirm-count` が **一致しないと 1 件も作らずに非 0 終了**します。
- **同時実行は常に 1 件**（全体で）。`--limit` を 2 以上にする場合は `--wait` が必須です。
- 実行前に人の承認が必要です。まず dry-run で対象件数と project ID を提示してください。

対象から外すもの
----------------
- **ダミー/テストデータ**（`skipped_dummy_or_test_data`）:
  campaign_url が取れておらず、かつ保存済みの公式サイト / メールドメインが
  予約・プレースホルダードメイン（example.com / localhost / .invalid など）のもの。
  判定は既存の `url_validation` / `is_dummy_domain` に委ね、
  **project ID・タイトル・メーカー名では判定しない。**
- **探索の起点が無いもの**（`skipped_no_research_seed`）:
  campaign_url / maker_url / 検証済み公式サイト のいずれも無い案件。
  **primary_email と maker_name は起点として認めない**（誤候補を生み、
  Brave のクォータを消費するだけで証拠が得られる見込みがないため）。

やらないこと
------------
- LQE の `run()` を呼ばない（`pre_research` / `pre_outreach` を再判定しない）
- メール下書きを作らない・Gmail API を呼ばない・メールを送らない
- `OUTREACH_GATE_MODE` を変更しない（enforce を有効化しない）
- 案件を archive しない・Ground Truth を変更しない
- 証跡が取れなくても推測で補完しない（取れないことは取れないまま記録する）

適性ゲートについて
------------------
メール探索系ジョブは `create_job` がサーバー側で適性ゲートを再判定し、不合格なら
`GateBlocked` を送出します。本スクリプトの `--reason` は既存の `create_job(override_reason=...)`
へそのまま渡り、「管理者の手動実行理由」として `contact_intelligence_jobs.gate_override_reason`
とジョブログに残ります。**理由なしでは不合格案件を実行できません。**

実行（backend ディレクトリで）:
    # 対象一覧だけ表示（ジョブを作らない）
    python scripts/requeue_contact_intelligence.py

    # 機械可読
    python scripts/requeue_contact_intelligence.py --json

    # 実行（人の承認後・1 件だけ）
    python scripts/requeue_contact_intelligence.py \\
        --execute --limit 1 --confirm-count 1 --reason "D-3 証跡再取得" --wait
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.contact_discovery import ContactDiscovery  # noqa: E402
from app.models.contact_intelligence_job import (  # noqa: E402
    CIJobStatus,
    CIJobType,
    ContactIntelligenceJob,
)
from app.models.project import Project  # noqa: E402
from app.models.sales_outreach import SalesOutreach  # noqa: E402
from app.services import campaign_url as campaign_url_mod  # noqa: E402
from app.services import contact_intelligence_service as ci  # noqa: E402
from app.services import contact_search_gate  # noqa: E402
# ダミー/プレースホルダー判定は既存の正本を再利用する（同じロジックを再実装しない）。
from app.services.contact_discovery_service import is_dummy_domain  # noqa: E402
from app.services.url_validation import business_url_reason  # noqa: E402

logger = logging.getLogger("requeue_contact_intelligence")

#: 再投入するジョブ種別。v2（人手順フロー）だけを対象にする。
JOB_TYPE = CIJobType.contact_discovery_v2.value

#: v2 が完了済みとみなす `contact_discoveries.v2_status` の値。
V2_COMPLETED = "completed"

#: 終了状態（ポーリング打ち切り条件）。
TERMINAL_STATUSES = frozenset(
    {
        CIJobStatus.completed.value,
        CIJobStatus.failed.value,
        CIJobStatus.cancelled.value,
        CIJobStatus.timed_out.value,
    }
)

DEFAULT_LIMIT = 1
DEFAULT_POLL_SECONDS = 15
DEFAULT_TIMEOUT_SECONDS = 900

# 終了コード
EXIT_OK = 0
EXIT_FAILED = 1        # 一部の案件で投入に失敗した
EXIT_USAGE = 2         # 引数不正 / confirm-count 不一致（ジョブは 1 件も作らない）
EXIT_ABORT = 3         # 安全を保証できないため全体停止（fail closed）

# 除外・結果の理由コード（画面と JSON で共通）
R_ENQUEUED = "enqueued"
R_NO_PROJECT = "skipped_no_project"
R_ARCHIVED = "skipped_archived"
R_V2_COMPLETED = "skipped_already_completed"
R_DUMMY = "skipped_dummy_or_test_data"
R_NO_SEED = "skipped_no_research_seed"
R_ACTIVE_JOB = "skipped_active_job"
R_OTHER_PROJECT_ACTIVE = "skipped_other_project_active"
R_CACHE = "skipped_cache_reused"
R_LIMIT = "skipped_over_limit"
R_NOT_SELECTED = "skipped_not_selected"
R_GATE_BLOCKED = "failed_gate_blocked"
R_ERROR = "failed_error"
R_TIMED_OUT = "timed_out"

#: テストから差し替えるためのフック（実 sleep を止められるようにする）。
_sleep = time.sleep


@dataclass
class Candidate:
    """SalesOutreach 行を持つ project 1 件ぶんの素材（メールアドレスは持たない）。"""

    project_id: int
    project_name: str | None = None
    outreach_statuses: list[str] = field(default_factory=list)
    outreach_rows: int = 0
    v2_status: str | None = None
    has_discovery_row: bool = False
    has_primary_email: bool = False
    latest_job: str | None = None       # "<job_type>:<status>" or None
    active_job_id: int | None = None
    gate_decision: str | None = None
    excluded_reason: str | None = None
    #: 探索の起点（"campaign_url" / "maker_url" / "official_site"）。無ければ None。
    seed_kind: str | None = None
    has_campaign_url: bool = False
    #: ダミー判定の根拠（属性名とラベルのみ。値そのものは持たない）。
    dummy_signals: list[str] = field(default_factory=list)
    exclusion_detail: str | None = None

    def to_public(self) -> dict:
        """外部へ出す形。**メールアドレス・API キー・Cookie は含めない。**"""
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "outreach_statuses": self.outreach_statuses,
            "outreach_rows": self.outreach_rows,
            "v2_status": self.v2_status,
            "has_discovery_row": self.has_discovery_row,
            "has_primary_email": self.has_primary_email,
            "has_campaign_url": self.has_campaign_url,
            "seed_kind": self.seed_kind,
            "dummy_signals": self.dummy_signals,
            "latest_job": self.latest_job,
            "gate_decision": self.gate_decision,
            "excluded_reason": self.excluded_reason,
            "exclusion_detail": self.exclusion_detail,
        }


class AbortRun(Exception):
    """安全を保証できないため全体を停止する（fail closed）。"""


# --------------------------------------------------------------------------- #
#  監査（実行前後の DB 件数）
# --------------------------------------------------------------------------- #
def audit_counts(db) -> dict:
    """実行前後で比較する件数。**読み取りのみ。**"""
    return {
        "projects": db.scalar(select(func.count()).select_from(Project)) or 0,
        "projects_archived": db.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.archived_at.is_not(None))
        )
        or 0,
        "contact_discoveries": db.scalar(
            select(func.count()).select_from(ContactDiscovery)
        )
        or 0,
        "ci_jobs": db.scalar(
            select(func.count()).select_from(ContactIntelligenceJob)
        )
        or 0,
    }


# --------------------------------------------------------------------------- #
#  対象抽出
# --------------------------------------------------------------------------- #
def _global_active_heavy(db) -> ContactIntelligenceJob | None:
    """**project を問わず** 進行中の重い探索ジョブを 1 本返す（同時実行 1 件の保証）。

    重い job_type の正本は `contact_intelligence_service._HEAVY_JOB_TYPES`。
    per-project の排他は `find_active_heavy` が担うが、ここでは全体の同時実行を
    1 件に抑えるため横断で見る。読み取りのみ。
    """
    stmt = (
        select(ContactIntelligenceJob)
        .where(
            ContactIntelligenceJob.job_type.in_(list(ci._HEAVY_JOB_TYPES)),
            ContactIntelligenceJob.status.in_(
                [CIJobStatus.queued.value, CIJobStatus.running.value]
            ),
        )
        .order_by(ContactIntelligenceJob.id.desc())
        .limit(1)
    )
    return db.scalar(stmt)


def _latest_job_label(db, project_id: int) -> str | None:
    job = db.scalar(
        select(ContactIntelligenceJob)
        .where(ContactIntelligenceJob.project_id == project_id)
        .order_by(ContactIntelligenceJob.id.desc())
        .limit(1)
    )
    return f"{job.job_type}:{job.status}" if job is not None else None


def _gate_decision(db, project: Project) -> str | None:
    """適性ゲートの判定を **保存せずに** 見る（`persist=False` で DB 書き込みなし）。

    `contact_search_gate.evaluate` は LQE の `run()` を呼ばない（履歴を書かない）。
    見えなくても処理は止めない（表示用の参考値）。
    """
    try:
        res = contact_search_gate.evaluate(db, project, persist=False)
        return res.get("contact_search_gate_decision")
    except Exception as exc:  # noqa: BLE001  表示用のため失敗を握る
        logger.warning("gate evaluate failed (project=%s): %s", project.id, exc)
        return None


def _email_domain(addr: str | None) -> str | None:
    """メールアドレスからドメインだけを取り出す（**ローカル部は捨てる**）。"""
    if not addr or "@" not in addr:
        return None
    return addr.rsplit("@", 1)[1].strip().lower() or None


def dummy_or_test_signals(
    *,
    campaign_url: str | None,
    official_site_url: str | None,
    email_domain: str | None,
) -> list[str]:
    """実案件として成立しないダミー/テストデータの根拠を返す（純粋関数）。

    **単独条件では除外しない。** 商品ページ URL（campaign_url）が取れていない案件に
    限り、保存済みの公式サイト / メールドメインが予約・プレースホルダードメイン
    （example.com / localhost / .invalid など）かを見る。campaign_url がある案件は
    実在の商品ページを持つので、ここでは絶対にダミー扱いしない。

    判定そのものは既存の正本（``url_validation`` / ``is_dummy_domain``）に委ねる。
    **project ID・タイトル・メーカー名では判定しない**（実案件を巻き込むため）。

    Returns:
        根拠のラベル列。空なら「ダミーとは言えない」。
    """
    if campaign_url:
        return []
    signals: list[str] = []
    if official_site_url:
        reason = business_url_reason(official_site_url)
        if reason:
            signals.append(f"official_site:{reason}")
    if email_domain and is_dummy_domain(email_domain):
        signals.append("email_domain:reserved")
    return signals


def research_seed(
    *,
    campaign_url: str | None,
    maker_url: str | None,
    official_site_url: str | None,
) -> str | None:
    """v2 探索の起点になりうる種別を優先順に返す。無ければ None。

    優先順位は 1. campaign_url → 2. maker_url → 3. 検証済み公式サイト。
    creator URL は campaign_url から派生するため、campaign_url が無い時点で
    creator URL も存在しない（別枠では扱わない）。

    **``primary_email`` と ``maker_name`` は起点として認めない。** メーカー名だけの
    検索は誤候補を生みやすく（実測: 韓国語社名で候補 8 件すべて不採用）、
    Brave のクォータを消費するだけで証拠が得られる合理的見込みがないため。

    いずれも「営業に使える URL」であることを既存の ``business_url_reason`` で確認する
    （プレースホルダー URL は起点として採用しない）。
    """
    for kind, url in (
        ("campaign_url", campaign_url),
        ("maker_url", maker_url),
        ("official_site", official_site_url),
    ):
        if url and business_url_reason(url) is None:
            return kind
    return None


def dedupe_rows(rows) -> dict[int, Candidate]:
    """`(project_id, outreach_status)` の行を project 単位へ畳む（純粋関数）。

    `sales_outreach.project_id` は unique だが、将来 unique が外れても
    **1 project につき 1 件しか投入しない**ことをここで保証する。
    """
    by_project: dict[int, Candidate] = {}
    for pid, status in rows:
        if pid is None:
            continue
        cand = by_project.get(pid)
        if cand is None:
            cand = Candidate(project_id=pid)
            by_project[pid] = cand
        cand.outreach_rows += 1
        if status and status not in cand.outreach_statuses:
            cand.outreach_statuses.append(status)
    return by_project


def collect_candidates(
    db, *, project_id: int | None = None, active_heavy=None
) -> tuple[list[Candidate], list[Candidate]]:
    """(対象, 除外) を返す。**読み取りのみ・外部 HTTP なし。**

    対象条件（すべて満たす）:
      1. SalesOutreach 行が存在する
      2. `projects.archived_at IS NULL`
      3. ContactDiscovery の `v2_status` が `completed` ではない
      4. 同一 project で重い CI ジョブが実行中ではない

    project の重複は排除する（SalesOutreach は 1 project に複数行ありうる）。
    """
    active_heavy = active_heavy or ci.find_active_heavy

    rows = db.execute(
        select(SalesOutreach.project_id, SalesOutreach.outreach_status).order_by(
            SalesOutreach.project_id, SalesOutreach.id
        )
    ).all()

    # project 重複を排除しつつ outreach 状態をまとめる
    by_project = dedupe_rows(rows)

    targets: list[Candidate] = []
    excluded: list[Candidate] = []

    for pid in sorted(by_project):
        cand = by_project[pid]
        if project_id is not None and pid != project_id:
            cand.excluded_reason = R_NOT_SELECTED
            excluded.append(cand)
            continue

        project = db.get(Project, pid)
        if project is None:
            cand.excluded_reason = R_NO_PROJECT
            excluded.append(cand)
            continue
        cand.project_name = project.title
        cand.latest_job = _latest_job_label(db, pid)

        disc = db.scalar(
            select(ContactDiscovery)
            .where(ContactDiscovery.project_id == pid)
            .order_by(ContactDiscovery.id.desc())
            .limit(1)
        )
        official_site: str | None = None
        email_domain: str | None = None
        if disc is not None:
            cand.has_discovery_row = True
            cand.v2_status = disc.v2_status
            # **アドレス自体は保持しない。** 有無とドメインだけを見る。
            primary = getattr(disc, "v2_primary_email", None) or getattr(
                disc, "primary_email", None
            )
            cand.has_primary_email = bool(primary)
            email_domain = _email_domain(primary)
            official_site = getattr(disc, "v2_official_site_url", None) or getattr(
                disc, "official_site_url", None
            )

        campaign = campaign_url_mod.campaign_url_of(project)
        cand.has_campaign_url = bool(campaign)

        if project.archived_at is not None:
            cand.excluded_reason = R_ARCHIVED
            excluded.append(cand)
            continue
        if cand.v2_status == V2_COMPLETED:
            cand.excluded_reason = R_V2_COMPLETED
            excluded.append(cand)
            continue

        # 実案件として成立しないダミー/テストデータを除外する。
        cand.dummy_signals = dummy_or_test_signals(
            campaign_url=campaign,
            official_site_url=official_site,
            email_domain=email_domain,
        )
        if cand.dummy_signals:
            cand.excluded_reason = R_DUMMY
            cand.exclusion_detail = "reserved_domain_and_no_campaign_url: " + ",".join(
                cand.dummy_signals
            )
            excluded.append(cand)
            continue

        # 探索の起点が無ければ再調査しても証拠を得られる見込みがない。
        cand.seed_kind = research_seed(
            campaign_url=campaign,
            maker_url=getattr(project, "maker_url", None),
            official_site_url=official_site,
        )
        if cand.seed_kind is None:
            cand.excluded_reason = R_NO_SEED
            cand.exclusion_detail = (
                "campaign_url / maker_url / 検証済み公式サイト のいずれも無い"
            )
            excluded.append(cand)
            continue

        # active heavy job の判定に失敗したら「実行中ではない」と決めつけない（fail closed）
        try:
            active = active_heavy(db, pid)
        except Exception as exc:  # noqa: BLE001
            raise AbortRun(
                f"active heavy job の判定に失敗しました (project={pid}): {exc}"
            ) from exc
        if active is not None:
            cand.active_job_id = active.id
            cand.excluded_reason = R_ACTIVE_JOB
            excluded.append(cand)
            continue

        cand.gate_decision = _gate_decision(db, project)
        targets.append(cand)

    return targets, excluded


def select_targets(targets: list[Candidate], limit: int) -> tuple[list[Candidate], list[Candidate]]:
    """`--limit` を適用し (選定, 上限超過で除外) を返す。project_id 昇順で決定的。"""
    if limit < 0:
        raise AbortRun("--limit は 0 以上で指定してください")
    selected = targets[:limit]
    overflow = []
    for cand in targets[limit:]:
        cand.excluded_reason = R_LIMIT
        overflow.append(cand)
    return selected, overflow


# --------------------------------------------------------------------------- #
#  投入
# --------------------------------------------------------------------------- #
def _wait_for_job(
    db, job_id: int, *, poll_seconds: int, timeout_seconds: int, sleep=None
) -> str:
    """1 本のジョブが終了状態になるまで待ち、最終 status を返す。

    タイムアウトしたら `timed_out` ではなく **実際の status** を返し、呼び出し側が
    「まだ動いている可能性がある」として後続を開始しない判断をする。
    """
    sleep = sleep or _sleep
    waited = 0
    while True:
        job = db.get(ContactIntelligenceJob, job_id)
        if job is not None:
            db.refresh(job)
            if job.status in TERMINAL_STATUSES:
                return job.status
        if waited >= timeout_seconds:
            return job.status if job is not None else "unknown"
        sleep(poll_seconds)
        waited += poll_seconds


def execute_targets(
    db,
    selected: list[Candidate],
    *,
    reason: str,
    wait: bool,
    poll_seconds: int,
    timeout_seconds: int,
    create_job=None,
    active_heavy=None,
    global_active=None,
    sleep=None,
) -> list[dict]:
    """1 件ずつ順次投入する。**同時実行は常に 1 件。**

    1 件の失敗で全体をクラッシュさせず、結果を集計して返す。ただし安全を保証できない
    事象（active job 判定不能・全体で他ジョブが動いている）は `AbortRun` で全体停止。
    """
    create_job = create_job or ci.create_job
    active_heavy = active_heavy or ci.find_active_heavy
    global_active = global_active or _global_active_heavy

    results: list[dict] = []
    for cand in selected:
        # 全体で同時実行 1 件。直前に横断チェックする（他 project の重いジョブも見る）。
        try:
            running = global_active(db)
        except Exception as exc:  # noqa: BLE001
            raise AbortRun(f"同時実行 1 件を保証できません: {exc}") from exc
        if running is not None:
            raise AbortRun(
                "他の重い探索ジョブが進行中のため停止しました "
                f"(job_id={running.id} project={running.project_id} "
                f"type={running.job_type} status={running.status})"
            )

        # 同一 project の並列起動禁止（投入直前に再確認）
        try:
            active = active_heavy(db, cand.project_id)
        except Exception as exc:  # noqa: BLE001
            raise AbortRun(
                f"active heavy job の判定に失敗しました (project={cand.project_id}): {exc}"
            ) from exc
        if active is not None:
            results.append(
                {
                    "project_id": cand.project_id,
                    "result": R_ACTIVE_JOB,
                    "job_id": active.id,
                    "detail": "投入直前に進行中ジョブを検出した",
                }
            )
            continue

        project = db.get(Project, cand.project_id)
        if project is None:
            results.append(
                {"project_id": cand.project_id, "result": R_NO_PROJECT, "job_id": None}
            )
            continue

        logger.info(
            "requeue: project=%s job_type=%s reason=%s",
            cand.project_id,
            JOB_TYPE,
            reason,
        )
        try:
            job, from_cache = create_job(
                db, project, JOB_TYPE, override_reason=reason
            )
        except contact_search_gate.GateBlocked as exc:
            # 理由を渡しても通らなかった＝ゲートの判断。推測で成功扱いにしない。
            results.append(
                {
                    "project_id": cand.project_id,
                    "result": R_GATE_BLOCKED,
                    "job_id": None,
                    "detail": str(exc)[:200],
                }
            )
            continue
        except Exception as exc:  # noqa: BLE001  1 件の失敗で全体を止めない
            # stack trace は stdout へ出さない（ログにだけ残す）
            logger.exception("create_job failed (project=%s)", cand.project_id)
            results.append(
                {
                    "project_id": cand.project_id,
                    "result": R_ERROR,
                    "job_id": None,
                    "detail": type(exc).__name__,
                }
            )
            continue

        if from_cache:
            # 24h 以内の completed を再利用しただけ＝新しい探索は走らない。
            results.append(
                {
                    "project_id": cand.project_id,
                    "result": R_CACHE,
                    "job_id": job.id,
                    "detail": "24h キャッシュを再利用したため新規探索は起動していない",
                }
            )
            continue

        entry = {
            "project_id": cand.project_id,
            "result": R_ENQUEUED,
            "job_id": job.id,
            "job_status": job.status,
        }
        results.append(entry)

        if not wait:
            continue

        status = _wait_for_job(
            db,
            job.id,
            poll_seconds=poll_seconds,
            timeout_seconds=timeout_seconds,
            sleep=sleep,
        )
        entry["job_status"] = status
        if status not in TERMINAL_STATUSES:
            # まだ動いている可能性がある。次の案件を勝手に開始しない（安全側）。
            entry["result"] = R_TIMED_OUT
            entry["detail"] = (
                f"{timeout_seconds}s 待っても終了しなかったため後続を開始しない"
            )
            raise AbortRun(
                f"job {job.id} (project={cand.project_id}) が "
                f"{timeout_seconds}s 以内に終了しませんでした。"
                "同時実行 1 件を保証するため後続を開始しません。"
            )
    return results


def summarize(results: list[dict], excluded: list[Candidate]) -> dict:
    """結果の内訳。Playwright / Brave の失敗は job 側の状態として扱い、推測で成功にしない。"""
    counted = {
        "attempted": len(results),
        "enqueued": sum(1 for r in results if r["result"] == R_ENQUEUED),
        "skipped": sum(1 for r in results if r["result"].startswith("skipped")),
        "failed": sum(1 for r in results if r["result"].startswith("failed")),
        "timed_out": sum(1 for r in results if r["result"] == R_TIMED_OUT),
        "active_job_skipped": sum(1 for r in results if r["result"] == R_ACTIVE_JOB)
        + sum(1 for c in excluded if c.excluded_reason == R_ACTIVE_JOB),
        "already_completed_skipped": sum(
            1 for c in excluded if c.excluded_reason == R_V2_COMPLETED
        ),
        "dummy_or_test_skipped": sum(
            1 for c in excluded if c.excluded_reason == R_DUMMY
        ),
        "no_research_seed_skipped": sum(
            1 for c in excluded if c.excluded_reason == R_NO_SEED
        ),
        "archived_skipped": sum(
            1 for c in excluded if c.excluded_reason == R_ARCHIVED
        ),
        "cache_reused": sum(1 for r in results if r["result"] == R_CACHE),
        "gate_blocked": sum(1 for r in results if r["result"] == R_GATE_BLOCKED),
    }
    return counted


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="requeue_contact_intelligence.py",
        description=(
            "Gmail 下書き候補・送信履歴のある案件へ Contact Intelligence v2 を再投入する。"
            " 既定は dry-run で、--execute が無ければジョブを 1 件も作らない。"
            " 外部 HTTP（Playwright / Brave）はワーカー側で発生する。"
            " 実行前に人の承認が必要。1 件ずつ実行し、enforce は有効化しない。"
            " LQE の再判定は別工程。証跡が取れなくても推測で補完しない。"
        ),
    )
    p.add_argument(
        "--execute",
        action="store_true",
        help="実際にジョブを作成する。指定が無ければ絶対に作らない（既定 dry-run）",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"最大処理件数（既定 {DEFAULT_LIMIT}）。2 以上にする場合は --wait が必須",
    )
    p.add_argument("--project-id", type=int, default=None, help="指定案件だけ対象にする")
    p.add_argument(
        "--confirm-count",
        type=int,
        default=None,
        help="実対象件数の確認。--execute 時必須。一致しなければ 1 件も作らず非 0 終了",
    )
    p.add_argument(
        "--reason", default=None, help="実行理由（監査ログとジョブへ記録）。--execute 時必須"
    )
    p.add_argument("--wait", action="store_true", help="1 件の完了を待ってから次へ進む")
    p.add_argument(
        "--poll-seconds",
        type=int,
        default=DEFAULT_POLL_SECONDS,
        help=f"--wait のポーリング間隔（既定 {DEFAULT_POLL_SECONDS} 秒）",
    )
    p.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            f"1 件あたりの最大待機時間（既定 {DEFAULT_TIMEOUT_SECONDS} 秒）。"
            "超えたら後続を開始せず停止する"
        ),
    )
    p.add_argument("--json", action="store_true", help="機械可読な結果を stdout へ出す")
    return p


def validate_args(args) -> str | None:
    """引数の整合。問題があればメッセージを返す（ジョブは 1 件も作らない）。"""
    if args.limit < 0:
        return "--limit は 0 以上で指定してください"
    if args.poll_seconds <= 0:
        return "--poll-seconds は 1 以上で指定してください"
    if args.timeout_seconds <= 0:
        return "--timeout-seconds は 1 以上で指定してください"
    if not args.execute:
        return None
    if not args.reason or not args.reason.strip():
        return "--execute には --reason（実行理由）が必須です"
    if args.confirm_count is None:
        return "--execute には --confirm-count が必須です"
    if args.limit > 1 and not args.wait:
        return (
            "--limit を 2 以上にする場合は --wait が必須です"
            "（同時実行 1 件を保証できないため）"
        )
    return None


def _print_targets(targets: list[Candidate], excluded: list[Candidate]) -> None:
    print(f"対象: {len(targets)} 件 / 除外: {len(excluded)} 件")
    print("")
    if targets:
        print("=== 対象 ===")
        print(
            f"{'pid':>5}  {'outreach':<16} {'v2_status':<12} {'gate':<14} "
            f"{'seed':<14} {'email':<6} {'latest_job':<34} name"
        )
        for c in targets:
            print(
                f"{c.project_id:>5}  {','.join(c.outreach_statuses)[:16]:<16} "
                f"{str(c.v2_status):<12} {str(c.gate_decision):<14} "
                f"{str(c.seed_kind):<14} "
                f"{'有' if c.has_primary_email else '無':<6} "
                f"{str(c.latest_job)[:34]:<34} {str(c.project_name)[:36]}"
            )
        print("")
        print("対象 project ID: " + ", ".join(str(c.project_id) for c in targets))
        print("")
    if excluded:
        print("=== 除外 ===")
        for c in excluded:
            detail = f" | {c.exclusion_detail}" if c.exclusion_detail else ""
            print(
                f"{c.project_id:>5}  {c.excluded_reason:<28} "
                f"campaign_url={'有' if c.has_campaign_url else '無'} "
                f"v2_status={c.v2_status} {str(c.project_name)[:30]}{detail}"
            )
        print("")
        dummies = [c for c in excluded if c.excluded_reason == R_DUMMY]
        if dummies:
            print(f"=== ダミー/テストデータ除外: {len(dummies)} 件 ===")
            for c in dummies:
                print(
                    f"  project_id={c.project_id} "
                    f"reason=reserved_domain_and_no_campaign_url "
                    f"signals={','.join(c.dummy_signals)}"
                )
            print("")
    print("※ メールアドレスは表示しません（有無とドメイン種別のみ）")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    args = build_parser().parse_args(argv)

    err = validate_args(args)
    if err:
        print(f"ERROR: {err}")
        print("ジョブは 1 件も作成していません。")
        return EXIT_USAGE

    try:
        db = SessionLocal()
    except Exception as exc:  # noqa: BLE001  DB 接続異常は全体停止
        print(f"ERROR: DB へ接続できません: {type(exc).__name__}")
        return EXIT_ABORT

    try:
        before = audit_counts(db)
        targets, excluded = collect_candidates(db, project_id=args.project_id)
        selected, overflow = select_targets(targets, args.limit)
        excluded = excluded + overflow
    except AbortRun as exc:
        print(f"ABORT: {exc}")
        print("ジョブは 1 件も作成していません。")
        db.close()
        return EXIT_ABORT
    except Exception as exc:  # noqa: BLE001  対象抽出の整合性異常は全体停止
        logger.exception("対象抽出に失敗しました")
        print(f"ABORT: 対象抽出に失敗しました: {type(exc).__name__}")
        print("ジョブは 1 件も作成していません。")
        db.close()
        return EXIT_ABORT

    payload: dict = {
        "mode": "execute" if args.execute else "dry_run",
        "job_type": JOB_TYPE,
        "limit": args.limit,
        "reason": args.reason,
        "counts_before": before,
        "matched": len(targets),
        "selected": len(selected),
        "target_project_ids": [c.project_id for c in selected],
        "targets": [c.to_public() for c in selected],
        "excluded": [c.to_public() for c in excluded],
    }

    if not args.execute:
        payload["results"] = []
        payload["summary"] = summarize([], excluded)
        payload["counts_after"] = audit_counts(db)
        payload["jobs_created"] = 0
        db.close()
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("=== dry-run（ジョブは作成しません） ===")
            print("")
            _print_targets(selected, excluded)
            print("")
            print(f"DB 件数（不変であること）: {before} -> {payload['counts_after']}")
            print("")
            print("実行するには人の承認を得たうえで:")
            print(
                f"  --execute --limit {max(len(selected), 1)} "
                f"--confirm-count {len(selected)} --reason \"<理由>\" --wait"
            )
        return EXIT_OK

    # --- ここから --execute ---------------------------------------------- #
    # 非対話環境でも使うため、対話確認には依存しない。件数一致だけを条件にする。
    print("=== 実行前の確認 ===")
    print(f"対象件数: {len(selected)} 件 / --confirm-count: {args.confirm_count}")
    print("対象 project ID: " + (", ".join(str(c.project_id) for c in selected) or "なし"))
    print(f"実行理由: {args.reason}")
    print("")

    if args.confirm_count != len(selected):
        print(
            f"ERROR: --confirm-count ({args.confirm_count}) が実対象件数 "
            f"({len(selected)}) と一致しません。"
        )
        print("ジョブは 1 件も作成していません。")
        db.close()
        return EXIT_USAGE

    if not selected:
        print("対象がありません。ジョブは 1 件も作成していません。")
        db.close()
        return EXIT_OK

    aborted: str | None = None
    try:
        results = execute_targets(
            db,
            selected,
            reason=args.reason,
            wait=args.wait,
            poll_seconds=args.poll_seconds,
            timeout_seconds=args.timeout_seconds,
        )
    except AbortRun as exc:
        aborted = str(exc)
        results = []
    except Exception as exc:  # noqa: BLE001
        logger.exception("実行中に想定外のエラー")
        aborted = f"想定外のエラー: {type(exc).__name__}"
        results = []

    payload["results"] = results
    payload["summary"] = summarize(results, excluded)
    payload["counts_after"] = audit_counts(db)
    payload["jobs_created"] = payload["summary"]["enqueued"]
    payload["aborted"] = aborted
    db.close()

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("=== 結果 ===")
        for k, v in payload["summary"].items():
            print(f"  {k}: {v}")
        print("")
        for r in results:
            print(
                f"  project={r['project_id']} result={r['result']} "
                f"job_id={r.get('job_id')} {r.get('detail', '')}"
            )
        print("")
        print(f"DB 件数: {before} -> {payload['counts_after']}")
        if aborted:
            print(f"\nABORT: {aborted}")
        print("\n※ LQE の再判定は行っていません（別工程）。enforce は有効化していません。")

    if aborted:
        return EXIT_ABORT
    if payload["summary"]["failed"] or payload["summary"]["timed_out"]:
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
