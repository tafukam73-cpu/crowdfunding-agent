"""AI 企業リサーチの業務ロジック。

リサーチャー（モック/Claude）で結果を作り、company_researches に保存する。
失敗（Claude の JSON パース失敗など）は research_status=failed として保存し、
アプリ全体は落とさない。最新の completed をメール生成に利用する。
"""
from __future__ import annotations

import logging

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.ai.company_researcher import CompanyResearcher, get_company_researcher
from app.models.company_research import CompanyResearch, ResearchStatus
from app.models.project import Project
from app.services import usage_service

logger = logging.getLogger("company_research")


def run_research(
    db: Session, project: Project, researcher: CompanyResearcher | None = None
) -> CompanyResearch:
    """企業リサーチを実行して保存する（実行のたびに履歴を追加）。

    成功時は research_status=completed、失敗時は failed で保存する。いずれも
    例外は外へ送出せず、保存した CompanyResearch を返す。
    """
    researcher = researcher or get_company_researcher()
    row = CompanyResearch(
        project_id=project.id,
        maker_name=project.maker_name,
        official_site_url=project.maker_url,
        project_url=project.source_url,
        research_status=ResearchStatus.pending.value,
        model=researcher.name,
    )
    db.add(row)

    try:
        result = researcher.research(project)
        row.research_status = ResearchStatus.completed.value
        row.maker_name = result.maker_name or project.maker_name
        row.official_site_url = result.official_site_url or project.maker_url
        row.project_url = result.project_url or project.source_url
        row.brand_summary = result.brand_summary
        row.company_mission = result.company_mission
        row.product_summary = result.product_summary
        row.key_product_features = result.key_product_features or None
        row.brand_strengths = result.brand_strengths or None
        row.differentiation_points = result.differentiation_points or None
        row.japan_market_fit = result.japan_market_fit
        row.personalized_compliment = result.personalized_compliment
        row.outreach_angles = result.outreach_angles or None
        row.risks_or_cautions = result.risks_or_cautions or None
        row.sources = result.sources or None
        row.model = result.model or researcher.name
        row.raw_notes = result.raw_notes or None

        usage_service.record_usage(
            db,
            kind="company_research",
            model=row.model,
            usage=getattr(researcher, "last_usage", None),
            project_id=project.id,
        )
    except Exception as exc:  # noqa: BLE001  失敗は failed として保存し継続
        logger.warning("company research failed (project=%s): %s", project.id, exc)
        row.research_status = ResearchStatus.failed.value
        row.raw_notes = str(exc)[:4000]

    db.commit()
    db.refresh(row)
    return row


def get_latest(db: Session, project_id: int) -> CompanyResearch | None:
    """案件の最新リサーチ（completed/failed/pending 問わず最新）を返す。"""
    stmt = (
        select(CompanyResearch)
        .where(CompanyResearch.project_id == project_id)
        .order_by(desc(CompanyResearch.created_at), desc(CompanyResearch.id))
        .limit(1)
    )
    return db.scalar(stmt)


def to_context(row: CompanyResearch | None) -> dict | None:
    """CompanyResearch をメール生成へ渡す dict に変換する（None なら None）。"""
    if row is None:
        return None
    return {
        "maker_name": row.maker_name,
        "brand_summary": row.brand_summary,
        "company_mission": row.company_mission,
        "product_summary": row.product_summary,
        "key_product_features": row.key_product_features or [],
        "brand_strengths": row.brand_strengths or [],
        "differentiation_points": row.differentiation_points or [],
        "japan_market_fit": row.japan_market_fit,
        "personalized_compliment": row.personalized_compliment,
        "outreach_angles": row.outreach_angles or [],
        "risks_or_cautions": row.risks_or_cautions or [],
        "sources": row.sources or [],
    }


def get_latest_completed(db: Session, project_id: int) -> CompanyResearch | None:
    """メール生成に使う「最新の completed なリサーチ」を返す（なければ None）。"""
    stmt = (
        select(CompanyResearch)
        .where(
            CompanyResearch.project_id == project_id,
            CompanyResearch.research_status == ResearchStatus.completed.value,
        )
        .order_by(desc(CompanyResearch.created_at), desc(CompanyResearch.id))
        .limit(1)
    )
    return db.scalar(stmt)
