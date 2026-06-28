"""日本販売状況チェックの業務ロジック。

チェッカー（モック/Claude）で結果を作り、japan_sales_checks に保存する。
営業価値（★1〜5）はチャネル status から決定的に算出する（compute_stars）。
失敗（Claude の JSON パース失敗など）は status=failed として保存し、アプリ全体は
落とさない。最新の completed をメール生成に反映する。
"""
from __future__ import annotations

import logging

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai.japan_sales_checker import (
    STATUS_FOUND,
    JapanSalesChecker,
    build_channels,
    build_search_queries,
    compute_stars,
    default_summary,
    get_japan_sales_checker,
)
from app.models.japan_sales_check import JapanSalesCheck, JapanSalesStatus
from app.models.project import Project
from app.services import usage_service

logger = logging.getLogger("japan_sales")

# 代理店/法人とみなすチャネル（メール文脈の has_distributor 判定に使う）
_DISTRIBUTOR_KEYS = ("distributor", "subsidiary")
_EC_KEYS = ("amazon", "rakuten", "yahoo")


def run_check(
    db: Session, project: Project, checker: JapanSalesChecker | None = None
) -> JapanSalesCheck:
    """日本販売状況チェックを実行して保存する（実行のたびに履歴を追加）。

    成功時は status=completed、失敗時は failed で保存する。いずれも例外は外へ
    送出せず、保存した JapanSalesCheck を返す。
    """
    checker = checker or get_japan_sales_checker()
    row = JapanSalesCheck(
        project_id=project.id,
        maker_id=project.maker_id,
        status=JapanSalesStatus.pending.value,
        model=checker.name,
    )
    db.add(row)

    try:
        result = checker.check(project)
        statuses = result.channel_statuses
        stars = compute_stars(statuses)
        channels = build_channels(
            project.title, project.maker_name, statuses, result.channel_notes
        )
        row.status = JapanSalesStatus.completed.value
        row.sales_value_stars = stars
        row.channels = channels
        row.search_queries = (
            build_search_queries(project.title, project.maker_name) or None
        )
        row.ai_comment = result.ai_comment or None
        row.summary = result.summary or default_summary(stars, statuses)
        row.model = result.model or checker.name
        row.notes = f"stars {stars}"

        usage_service.record_usage(
            db,
            kind="japan_sales",
            model=row.model,
            usage=getattr(checker, "last_usage", None),
            project_id=project.id,
        )
    except Exception as exc:  # noqa: BLE001  失敗は failed として保存し継続
        logger.warning("japan sales check failed (project=%s): %s", project.id, exc)
        row.status = JapanSalesStatus.failed.value
        row.error = str(exc)[:4000]

    db.commit()
    db.refresh(row)
    return row


def get_latest(db: Session, project_id: int) -> JapanSalesCheck | None:
    """案件の最新チェック（status 問わず最新）を返す。"""
    stmt = (
        select(JapanSalesCheck)
        .where(JapanSalesCheck.project_id == project_id)
        .order_by(desc(JapanSalesCheck.created_at), desc(JapanSalesCheck.id))
        .limit(1)
    )
    return db.scalar(stmt)


def get_latest_completed(db: Session, project_id: int) -> JapanSalesCheck | None:
    """メール生成に使う「最新の completed なチェック」を返す（なければ None）。"""
    stmt = (
        select(JapanSalesCheck)
        .where(
            JapanSalesCheck.project_id == project_id,
            JapanSalesCheck.status == JapanSalesStatus.completed.value,
        )
        .order_by(desc(JapanSalesCheck.created_at), desc(JapanSalesCheck.id))
        .limit(1)
    )
    return db.scalar(stmt)


def to_email_context(row: JapanSalesCheck | None) -> dict | None:
    """JapanSalesCheck をメール生成へ渡す dict に変換する（None なら None）。

    本文に「日本に既存代理店が見つからず参入機会がある」旨を反映できるよう、
    代理店の有無・EC 販売の有無・営業価値・コメントを要約する。
    """
    if row is None:
        return None
    channels = row.channels or []
    by_key = {c.get("channel"): c.get("status") for c in channels}
    has_distributor = any(by_key.get(k) == STATUS_FOUND for k in _DISTRIBUTOR_KEYS)
    sold_in_japan = any(by_key.get(k) == STATUS_FOUND for k in _EC_KEYS)
    stars = row.sales_value_stars or 0
    return {
        "stars": stars,
        "summary": row.summary or "",
        "ai_comment": row.ai_comment or "",
        "has_distributor": has_distributor,
        "sold_in_japan": sold_in_japan,
        # 代理店・EC 販売ともに未確認＝日本未上陸の見込み（参入機会を訴求）
        "no_japan_presence": (not has_distributor) and (not sold_in_japan),
    }
