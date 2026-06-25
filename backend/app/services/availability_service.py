"""日本未上陸判定の業務ロジック。

5 サイトを検索 → ヒット根拠を保存 → 最大一致スコアから判定を集計。
判定は履歴（availability_checks）として残し、案件の最新判定を更新する。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.availability import get_search_providers
from app.models.availability import (
    AvailabilityCheck,
    AvailabilityHit,
    AvailabilityVerdict,
)
from app.models.project import Project

logger = logging.getLogger("availability")

ENGINE = "mock-availability-v1"

# 判定しきい値（最大一致スコア）
STRONG = 80    # これ以上 → 日本販売済み
MODERATE = 50  # これ以上 → 可能性あり


def _verdict(max_score: int) -> AvailabilityVerdict:
    if max_score >= STRONG:
        return AvailabilityVerdict.sold
    if max_score >= MODERATE:
        return AvailabilityVerdict.possible
    return AvailabilityVerdict.not_landed


def _build_query(project: Project) -> str:
    parts = [project.title or ""]
    if project.maker_name:
        parts.append(project.maker_name)
    return " ".join(p for p in parts if p).strip()


def check_project(db: Session, project: Project) -> AvailabilityCheck:
    """案件の日本販売状況を判定し、根拠とともに保存する。"""
    query = _build_query(project)

    all_hits = []
    for provider in get_search_providers():
        try:
            all_hits.extend(provider.search(query))
        except Exception as exc:  # noqa: BLE001  1 サイト失敗は無視して継続
            logger.warning("search failed (%s): %s", provider.site, exc)

    max_score = max((h.match_score for h in all_hits), default=0)
    verdict = _verdict(max_score)

    sites_hit = sorted({h.site for h in all_hits})
    if all_hits:
        top = max(all_hits, key=lambda h: h.match_score)
        summary = (
            f"{len(sites_hit)}/5 サイトでヒット（{', '.join(sites_hit)}）。"
            f"最大一致スコア {max_score}（{top.site}）。"
        )
    else:
        summary = "5 サイトでヒットなし。日本未上陸の可能性が高い。"

    check = AvailabilityCheck(
        project_id=project.id,
        verdict=verdict.value,
        score=max_score,
        query=query,
        summary=summary,
        engine=ENGINE,
    )
    db.add(check)
    db.flush()  # check.id を採番

    for h in all_hits:
        db.add(
            AvailabilityHit(
                check_id=check.id,
                site=h.site,
                title=h.title,
                url=h.url,
                match_score=h.match_score,
            )
        )

    project.latest_availability = verdict.value
    project.latest_availability_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(check)
    return check


def list_checks(db: Session, project_id: int) -> list[AvailabilityCheck]:
    stmt = (
        select(AvailabilityCheck)
        .where(AvailabilityCheck.project_id == project_id)
        .order_by(desc(AvailabilityCheck.created_at), desc(AvailabilityCheck.id))
    )
    return list(db.scalars(stmt))


def list_hits(db: Session, check_id: int) -> list[AvailabilityHit]:
    stmt = (
        select(AvailabilityHit)
        .where(AvailabilityHit.check_id == check_id)
        .order_by(desc(AvailabilityHit.match_score))
    )
    return list(db.scalars(stmt))
