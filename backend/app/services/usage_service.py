"""使用量ログの記録と集計。"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import pricing
from app.models.usage_log import UsageLog


def record_usage(
    db: Session,
    *,
    kind: str,
    model: str,
    usage: dict | None,
    project_id: int | None = None,
) -> UsageLog | None:
    """Claude の usage からログを追加する（コミットは呼び出し側）。

    usage が None（モック実行）の場合は記録しない。
    """
    if not usage:
        return None
    in_tokens = int(usage.get("input_tokens", 0))
    out_tokens = int(usage.get("output_tokens", 0))
    cost = Decimal(str(round(pricing.cost_usd(model, in_tokens, out_tokens), 6)))
    log = UsageLog(
        kind=kind,
        model=model,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        cost_usd=cost,
        project_id=project_id,
    )
    db.add(log)
    return log


def _aggregate(db: Session, since: datetime | None) -> dict:
    stmt = select(
        func.coalesce(func.sum(UsageLog.cost_usd), 0),
        func.coalesce(func.sum(UsageLog.input_tokens), 0),
        func.coalesce(func.sum(UsageLog.output_tokens), 0),
        func.count(UsageLog.id),
    )
    if since is not None:
        stmt = stmt.where(UsageLog.created_at >= since)
    cost, in_tokens, out_tokens, calls = db.execute(stmt).one()
    return {
        "cost_usd": float(cost or 0),
        "input_tokens": int(in_tokens or 0),
        "output_tokens": int(out_tokens or 0),
        "calls": int(calls or 0),
    }


def summary(db: Session) -> dict:
    """本日 / 今月 / 累計 のコストとトークンを集計（UTC基準）。"""
    now = datetime.now(timezone.utc)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "today": _aggregate(db, start_today),
        "month": _aggregate(db, start_month),
        "total": _aggregate(db, None),
    }
