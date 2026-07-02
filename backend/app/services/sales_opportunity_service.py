"""営業案件管理の業務ロジック（Contact Intelligence v5）。

Contact Intelligence（連絡先探索 + AI 営業分析）の結果を営業案件（SalesOpportunity）
に変換して管理する。作成は contact_discovery 単位で冪等（重複作成しない）。営業
スコア・優先度・推奨チャネル・次アクション・主要連絡先を作成時に引き継ぐ。

既存の CRM（Maker/Contact/SalesActivity）とは別軸で管理し、自動同期は行わない。
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import asc, desc, select
from sqlalchemy.orm import Session

from app.models.contact_discovery import ContactDiscovery
from app.models.project import Project
from app.models.sales_opportunity import SalesOpportunity, SalesOpportunityStatus
from app.services import contact_discovery_service as cds

logger = logging.getLogger("sales_opportunity")

# 優先度のしきい値（営業スコア 0〜100 → high / medium / low）
_PRIORITY_HIGH = 70
_PRIORITY_MEDIUM = 40

# 主要 SNS を選ぶ優先順位（営業に使いやすい順）
_SOCIAL_PREFERENCE = ("linkedin", "instagram", "facebook", "twitter", "youtube", "tiktok")

_VALID_STATUS = {s.value for s in SalesOpportunityStatus}
_VALID_SORT = {"score", "due_date", "created"}


def priority_from_score(score: int | None) -> str:
    """営業スコア（0〜100）から優先度ラベルを導く。"""
    s = int(score or 0)
    if s >= _PRIORITY_HIGH:
        return "high"
    if s >= _PRIORITY_MEDIUM:
        return "medium"
    return "low"


def _first_social(cd: ContactDiscovery) -> str | None:
    """探索結果の各レイヤーから主要 SNS URL を 1 つ選ぶ（linkedin 優先）。"""
    merged: dict[str, str] = {}
    # 代表カラム（instagram_url 等）
    for plat in ("linkedin", "instagram", "facebook", "twitter", "youtube"):
        url = getattr(cd, f"{plat}_url", None)
        if url:
            merged.setdefault(plat, url)
    # 各レイヤーの socials dict
    for attr in (
        "web_discovered_socials", "recursive_socials",
        "doc_reader_socials", "search_agent_socials", "discovered_socials",
    ):
        for k, v in (getattr(cd, attr, None) or {}).items():
            if v:
                merged.setdefault(k, v)
    for plat in _SOCIAL_PREFERENCE:
        if merged.get(plat):
            return merged[plat]
    # 上記以外のプラットフォームでも 1 つあれば返す
    return next(iter(merged.values()), None)


def _derive_fields(db: Session, cd: ContactDiscovery) -> dict:
    """Contact Intelligence 結果（+ 案件）から営業案件の初期値を組み立てる。

    v4（AI 営業分析）で sales_score / sales_priority / next_action が付与されていれば
    それを優先し、無ければ Contact Intelligence（v3）の値から導出する（前方/後方互換）。
    """
    project = db.get(Project, cd.project_id) if cd.project_id else None

    # 企業名：AI 読解の会社名 → 案件のメーカー名
    company = (
        getattr(cd, "doc_reader_official_company_name", None)
        or (project.maker_name if project else None)
    )
    product = project.title if project else None

    # 公式サイト（プラットフォーム URL は除外）
    website = (
        cds.official_site_or_none(getattr(cd, "official_site_url", None))
        or cds.official_site_or_none(getattr(cd, "search_agent_official_site_url", None))
        or cds.official_site_or_none(getattr(cd, "doc_reader_official_site_url", None))
    )

    # 営業スコア：v4 の sales_score → contactability_score → web/confidence
    score = (
        getattr(cd, "sales_score", None)
        if getattr(cd, "sales_score", None) is not None
        else getattr(cd, "contactability_score", None)
    )
    if score is None:
        score = getattr(cd, "web_confidence_score", None) or getattr(cd, "confidence_score", None)
    score = int(score) if score is not None else 0

    priority = getattr(cd, "sales_priority", None) or priority_from_score(score)

    channel = (
        getattr(cd, "recommended_channel", None)
        or getattr(cd, "web_recommended_channel", None)
        or getattr(cd, "search_agent_recommended_channel", None)
        or getattr(cd, "doc_reader_recommended_channel", None)
    )

    # 主要メール：営業推奨ランキングの最上位 → 各レイヤーの primary
    ranked = cds.build_sales_contacts(cd)
    email = (
        (ranked[0]["email"] if ranked else None)
        or getattr(cd, "primary_email", None)
        or getattr(cd, "web_primary_email", None)
    )

    form = (
        getattr(cd, "primary_contact_form_url", None)
        or getattr(cd, "web_primary_contact_form_url", None)
    )

    next_action = (
        getattr(cd, "next_action", None)
        or getattr(cd, "recommended_action", None)
    )

    return {
        "company_name": company,
        "product_name": product,
        "website_url": website,
        "sales_score": score,
        "sales_priority": priority,
        "recommended_channel": channel,
        "primary_email": email,
        "contact_form_url": form,
        "primary_social_url": _first_social(cd),
        "next_action": next_action,
    }


def get_by_contact_discovery(
    db: Session, contact_discovery_id: int
) -> SalesOpportunity | None:
    return db.scalar(
        select(SalesOpportunity).where(
            SalesOpportunity.contact_discovery_id == contact_discovery_id
        )
    )


def get(db: Session, opportunity_id: int) -> SalesOpportunity | None:
    return db.get(SalesOpportunity, opportunity_id)


def create_from_contact_discovery(
    db: Session, contact_discovery_id: int
) -> tuple[SalesOpportunity, bool]:
    """Contact Intelligence 結果から営業案件を作成する（冪等）。(案件, created) を返す。

    同じ contact_discovery_id で既に案件があれば作成せずそれを返す（重複作成防止）。
    """
    existing = get_by_contact_discovery(db, contact_discovery_id)
    if existing is not None:
        return existing, False

    cd = db.get(ContactDiscovery, contact_discovery_id)
    if cd is None:
        raise ValueError("連絡先探索が見つかりません")

    fields = _derive_fields(db, cd)
    opp = SalesOpportunity(
        contact_discovery_id=contact_discovery_id,
        status=SalesOpportunityStatus.not_started.value,
        **fields,
    )
    db.add(opp)
    db.commit()
    db.refresh(opp)
    logger.info(
        "sales opportunity created: id=%s cd=%s score=%s priority=%s",
        opp.id, contact_discovery_id, opp.sales_score, opp.sales_priority,
    )
    return opp, True


# 未指定を表すセンチネル（None を「クリア」と区別するため）
_UNSET = object()


def update(
    db: Session,
    opportunity_id: int,
    *,
    status: str | object = _UNSET,
    next_action: str | None | object = _UNSET,
    next_action_due_date: date | None | object = _UNSET,
    notes: str | None | object = _UNSET,
) -> SalesOpportunity | None:
    """営業案件の status / next_action / next_action_due_date / notes を更新する。

    渡されたフィールドのみ更新する（None は明示的なクリアとして扱う）。
    不正な status は ValueError。
    """
    opp = db.get(SalesOpportunity, opportunity_id)
    if opp is None:
        return None
    if status is not _UNSET:
        if status not in _VALID_STATUS:
            raise ValueError(f"未知の status: {status}")
        opp.status = status  # type: ignore[assignment]
    if next_action is not _UNSET:
        opp.next_action = next_action  # type: ignore[assignment]
    if next_action_due_date is not _UNSET:
        opp.next_action_due_date = next_action_due_date  # type: ignore[assignment]
    if notes is not _UNSET:
        opp.notes = notes  # type: ignore[assignment]
    db.commit()
    db.refresh(opp)
    return opp


def list_opportunities(
    db: Session,
    *,
    status: str | None = None,
    sales_priority: str | None = None,
    min_score: int | None = None,
    sort: str = "score",
) -> list[SalesOpportunity]:
    """営業案件一覧を取得する（フィルター・並び替え対応）。

    sort:
      - "score"（既定）: 営業スコア降順
      - "due_date"      : 次回アクション期限の近い順（未設定は末尾）
      - "created"       : 作成日時の新しい順
    """
    stmt = select(SalesOpportunity)
    if status:
        stmt = stmt.where(SalesOpportunity.status == status)
    if sales_priority:
        stmt = stmt.where(SalesOpportunity.sales_priority == sales_priority)
    if min_score is not None:
        stmt = stmt.where(SalesOpportunity.sales_score >= min_score)

    if sort == "due_date":
        # 期限の近い順。未設定（NULL）は末尾へ（DB 非依存の並び）。
        stmt = stmt.order_by(
            SalesOpportunity.next_action_due_date.is_(None),
            asc(SalesOpportunity.next_action_due_date),
            desc(SalesOpportunity.sales_score),
        )
    elif sort == "created":
        stmt = stmt.order_by(desc(SalesOpportunity.created_at), desc(SalesOpportunity.id))
    else:  # "score"（既定）
        stmt = stmt.order_by(
            desc(SalesOpportunity.sales_score), desc(SalesOpportunity.id)
        )
    return list(db.scalars(stmt).all())
