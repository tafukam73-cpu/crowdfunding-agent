"""取得アラートの組み立てと配信。

監視レポート（scrape_monitor）から異常サイトを抽出してアラートを作り、
設定済みの通知プロバイダー（Slack 等）へ配信する。日次/手動ジョブ完了後に
呼ばれる。通知先未設定・異常なしの場合は何もしない。

通知トリガー：いずれかのサイトで「構造変化の疑い」を検知したとき
（structure_change_suspected）。本文には構造変化に加えて成功率低下サイトも
まとめて載せる。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.project import SourceSite
from app.notifications import Alert, NotifierError, SiteAlert, get_notifiers
from app.services import scrape_monitor
from app.services.scrape_monitor import SITE_LABELS, SiteStats

logger = logging.getLogger("alert_service")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _admin_url() -> str | None:
    base = (settings.app_base_url or settings.frontend_origin or "").rstrip("/")
    return base or None


def _issues(s: SiteStats) -> list[str]:
    issues: list[str] = []
    if s.structure_change_suspected:
        issues.append("構造変化検知")
    if s.degraded:
        issues.append("成功率低下")
    return issues


def _to_site_alert(s: SiteStats) -> SiteAlert:
    return SiteAlert(
        site=s.site.value,
        site_label=SITE_LABELS.get(s.site.value, s.site.value),
        issues=_issues(s),
        error_breakdown=(
            f"通信{s.network_errors} / 構造{s.structure_errors} / 他{s.unknown_errors}"
            f"（403 {s.http_403_count}）"
        ),
        success_rate=s.success_rate,
        last_error_at=s.last_failure_at,
        last_structure_error_at=s.last_structure_error_at,
    )


def build_alert(report: scrape_monitor.MonitorReport) -> Alert | None:
    """レポートからアラートを組み立てる。通知不要なら None。

    トリガー：構造変化の疑いがある場合のみ。対象サイトには構造変化または
    成功率低下のいずれかに該当するものを含める。
    """
    if not report.structure_change_suspected:
        return None

    abnormal = [
        s for s in report.sites if s.structure_change_suspected or s.degraded
    ]
    if not abnormal:
        return None

    return Alert(
        title="クラファン取得で異常を検知しました",
        window=report.window,
        admin_url=_admin_url(),
        sites=[_to_site_alert(s) for s in abnormal],
    )


def notify_if_needed(db: Session, window: int | None = None) -> dict:
    """監視レポートを評価し、必要なら通知する。

    Returns: {"notified": bool, "via": [...], "sites": [...], "reason": str?}
    """
    window = window or settings.alert_window
    report = scrape_monitor.report(db, window=window)
    alert = build_alert(report)
    if alert is None:
        return {"notified": False, "reason": "no structure change detected"}

    notifiers = get_notifiers()
    if not notifiers:
        # 通知先未設定：検知はしたが送信はしない（ログのみ）
        logger.info(
            "structure change detected but no notifier configured (%d sites)",
            len(alert.sites),
        )
        return {
            "notified": False,
            "reason": "no notifier configured",
            "sites": [s.site for s in alert.sites],
        }

    sent: list[str] = []
    for n in notifiers:
        try:
            n.send(alert)
            sent.append(n.name)
        except NotifierError as exc:  # 1 プロバイダーの失敗で全体を止めない
            logger.warning("notifier '%s' failed: %s", n.name, exc)

    return {
        "notified": bool(sent),
        "via": sent,
        "sites": [s.site for s in alert.sites],
    }


def send_test() -> dict:
    """設定済みの通知先へテストアラートを送る（疎通確認用）。

    実際の監視結果に依存しないサンプルを送信する。通知先未設定なら何もしない。
    """
    notifiers = get_notifiers()
    if not notifiers:
        return {"notified": False, "reason": "no notifier configured"}

    alert = Alert(
        title="【テスト】クラファン取得監視の通知テスト",
        window=settings.alert_window,
        admin_url=_admin_url(),
        sites=[
            SiteAlert(
                site=SourceSite.kickstarter.value,
                site_label=SITE_LABELS[SourceSite.kickstarter.value],
                issues=["構造変化検知", "成功率低下"],
                error_breakdown="通信1 / 構造1 / 他0（403 1）",
                success_rate=0.5,
                last_error_at=_now(),
                last_structure_error_at=_now(),
            )
        ],
    )
    sent: list[str] = []
    for n in notifiers:
        try:
            n.send(alert)
            sent.append(n.name)
        except NotifierError as exc:
            logger.warning("test notifier '%s' failed: %s", n.name, exc)
    return {"notified": bool(sent), "via": sent}
