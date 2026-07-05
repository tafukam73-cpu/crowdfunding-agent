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
from app.services.url_validation import (
    filter_business_urls,
    first_valid_url,
    is_valid_business_url,
    verify_url_ok,
)

logger = logging.getLogger("company_research")


def _resolve_official_site_url(
    db: Session, project: Project, result_url: str | None
) -> str | None:
    """公式サイト URL を優先順（project.website → 連絡先探索 → research結果）で解決する。

    ダミー/プレースホルダー URL は採用しない。いずれも無効なら None を返し、
    UI は「公式サイト未確認」を表示する。
    """
    contact_site: str | None = None
    try:
        from app.services import contact_discovery_service as cds

        row = cds.get_latest(db, project.id)
        if row is not None:
            contact_site = row.official_site_url
    except Exception as exc:  # noqa: BLE001  連絡先探索が無くても続行
        logger.info("official site lookup skipped (project=%s): %s", project.id, exc)

    return first_valid_url(project.maker_url, contact_site, result_url)


def _clean_sources(urls: list[str] | None, *, verify: bool) -> list[str] | None:
    """参照元 URL からダミーを除外し、任意で 200 OK のみに絞る（要件 1・5・7）。"""
    urls = filter_business_urls(urls)
    if verify:
        urls = [u for u in urls if verify_url_ok(u)]
    return urls or None


def run_research(
    db: Session,
    project: Project,
    researcher: CompanyResearcher | None = None,
    *,
    verify_urls: bool = True,
) -> CompanyResearch:
    """企業リサーチを実行して保存する（実行のたびに履歴を追加）。

    成功時は research_status=completed、失敗時は failed で保存する。いずれも
    例外は外へ送出せず、保存した CompanyResearch を返す。

    参照元 URL・公式サイト URL は必ず URL バリデーションを通し、example.com 等の
    ダミー/プレースホルダーは保存しない（AI が生成した場合もここで除外）。
    verify_urls=True のときは 200 OK（2xx/3xx）で到達できる URL のみ残す。
    """
    researcher = researcher or get_company_researcher()
    # 初期値もダミーを弾いて保存（pending 表示でも example URL を出さない）
    row = CompanyResearch(
        project_id=project.id,
        maker_name=project.maker_name,
        official_site_url=project.maker_url
        if is_valid_business_url(project.maker_url)
        else None,
        project_url=project.source_url
        if is_valid_business_url(project.source_url)
        else None,
        research_status=ResearchStatus.pending.value,
        model=researcher.name,
    )
    db.add(row)

    try:
        result = researcher.research(project)
        row.research_status = ResearchStatus.completed.value
        row.maker_name = result.maker_name or project.maker_name
        # 公式サイトは優先順で解決し、ダミーは採用しない（無ければ None＝未確認）
        row.official_site_url = _resolve_official_site_url(
            db, project, result.official_site_url
        )
        # 案件 URL は実 campaign_url を使い、ダミー/プレースホルダーは採用しない
        row.project_url = first_valid_url(result.project_url, project.source_url)
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
        # 参照元 URL はダミー除外＋（可能なら）200 OK のみに絞る
        row.sources = _clean_sources(result.sources, verify=verify_urls)
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
