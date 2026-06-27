"""取得成功率・構造変化の監視。

scrape_runs を集計し、サイトごとの直近の取得成功率とエラー種別の内訳を返す。
構造変化（error_kind=structure）は「セレクタ/API 仕様の変化」を示す重要シグナル
として、別フラグで強調する。

ダッシュボード/アラート（GET /scrape/stats）から利用する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.project import SourceSite
from app.models.scrape_run import ErrorKind, ScrapeRun, ScrapeStatus

# 監視対象サイト（実スクレイパーを持つ収集対象）
MONITORED_SITES: list[SourceSite] = [
    SourceSite.kickstarter,
    SourceSite.indiegogo,
    SourceSite.ulule,
    SourceSite.makuake,
    SourceSite.greenfunding,
]

# サイト識別子 → 表示名（通知・ログ用）
SITE_LABELS: dict[str, str] = {
    SourceSite.kickstarter.value: "Kickstarter",
    SourceSite.indiegogo.value: "Indiegogo",
    SourceSite.wadiz.value: "Wadiz",
    SourceSite.ulule.value: "Ulule",
    SourceSite.makuake.value: "Makuake",
    SourceSite.greenfunding.value: "GreenFunding",
}

# 成功率がこれを下回ると要注意（アラート判定）
DEGRADED_THRESHOLD = 0.5


@dataclass
class SiteStats:
    site: SourceSite
    window: int                       # 集計対象にした直近 run 件数
    total: int = 0                    # 集計できた run 件数（running を除く完了 run）
    success: int = 0
    network_errors: int = 0
    structure_errors: int = 0
    unknown_errors: int = 0
    # 403（ボット対策/レート制限の代表的サイン）の発生件数。network エラーの内数。
    http_403_count: int = 0
    success_rate: float | None = None  # success / total（total=0 なら None）
    last_status: str | None = None
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None   # 最終成功日時
    last_failure_at: datetime | None = None   # 最終失敗日時
    # 構造変化の疑い：window 内に structure エラーがあれば True（要対応シグナル）
    structure_change_suspected: bool = False
    last_structure_error_at: datetime | None = None
    # 成功率低下の疑い（しきい値割れ かつ 1 件以上のエラー）
    degraded: bool = False

    @property
    def errors(self) -> int:
        return self.network_errors + self.structure_errors + self.unknown_errors


def _run_time(run: ScrapeRun) -> datetime:
    return run.finished_at or run.started_at


def site_stats(db: Session, site: SourceSite, window: int = 20) -> SiteStats:
    """1 サイトの直近 window 件（完了 run）の成功率・エラー内訳を集計する。"""
    runs = list(
        db.scalars(
            select(ScrapeRun)
            .where(
                ScrapeRun.site == site.value,
                ScrapeRun.status != ScrapeStatus.running.value,
            )
            .order_by(desc(ScrapeRun.started_at), desc(ScrapeRun.id))
            .limit(window)
        )
    )

    stats = SiteStats(site=site, window=window)
    if runs:
        latest = runs[0]
        stats.last_status = latest.status
        stats.last_run_at = _run_time(latest)

    # runs は新しい順。各「最終◯◯日時」は最初に見つかったものを採用する。
    for run in runs:
        stats.total += 1
        if run.status == ScrapeStatus.success.value:
            stats.success += 1
            if stats.last_success_at is None:
                stats.last_success_at = _run_time(run)
            continue

        # --- エラー run ---
        if stats.last_failure_at is None:
            stats.last_failure_at = _run_time(run)
        # 403 はエラー本文から検出（network エラーの内数として別カウント）
        if run.error and "403" in run.error:
            stats.http_403_count += 1

        # 種別で内訳。古いデータ（error_kind=null）は unknown 扱い。
        kind = run.error_kind or ErrorKind.unknown.value
        if kind == ErrorKind.network.value:
            stats.network_errors += 1
        elif kind == ErrorKind.structure.value:
            stats.structure_errors += 1
            if stats.last_structure_error_at is None:
                stats.last_structure_error_at = _run_time(run)
        else:
            stats.unknown_errors += 1

    if stats.total:
        stats.success_rate = round(stats.success / stats.total, 4)

    # 構造変化の疑い：window 内に structure エラーがある場合に立てる。
    # ただし、最終成功が最後の構造エラーより新しければ「復旧済み」とみなして
    # 過剰な警告を抑止する（古い構造エラーで出続けないようにする）。
    recovered = bool(
        stats.last_success_at
        and stats.last_structure_error_at
        and stats.last_success_at > stats.last_structure_error_at
    )
    stats.structure_change_suspected = stats.structure_errors > 0 and not recovered
    stats.degraded = (
        stats.success_rate is not None
        and stats.errors > 0
        and stats.success_rate < DEGRADED_THRESHOLD
    )
    return stats


@dataclass
class MonitorReport:
    window: int
    threshold: float
    sites: list[SiteStats] = field(default_factory=list)

    @property
    def structure_change_suspected(self) -> bool:
        return any(s.structure_change_suspected for s in self.sites)

    @property
    def degraded(self) -> bool:
        return any(s.degraded for s in self.sites)


def report(
    db: Session,
    window: int = 20,
    sites: list[SourceSite] | None = None,
) -> MonitorReport:
    """全監視対象サイトの集計レポートを返す。"""
    targets = sites or MONITORED_SITES
    return MonitorReport(
        window=window,
        threshold=DEGRADED_THRESHOLD,
        sites=[site_stats(db, s, window=window) for s in targets],
    )
