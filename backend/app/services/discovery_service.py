"""発掘商品候補の業務ロジック（Discovery Engine v1-1）。

海外クラウドファンディング等から発掘した商品候補を保存・一覧取得する土台。
今回は AI スコアリング・実サイト取得・フロント画面は実装せず、CRUD の土台のみ。

source_url をキーに重複登録を防ぐ（同一 URL は既存を返す）。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy import asc, desc, select
from sqlalchemy.orm import Session

from app.models.discovered_product import (
    DiscoveredProduct,
    DiscoveredProductStatus,
    DiscoverySourcePlatform,
)
from app.models.project import SourceSite
from app.services import discovery_scoring_service

logger = logging.getLogger("discovery")

_VALID_PLATFORM = {p.value for p in DiscoverySourcePlatform}
_VALID_STATUS = {s.value for s in DiscoveredProductStatus}
_VALID_SORT = {"score", "created"}

# 未指定を表すセンチネル（None を「クリア」と区別するため）
_UNSET = object()


def get(db: Session, product_id: int) -> DiscoveredProduct | None:
    return db.get(DiscoveredProduct, product_id)


def get_by_source_url(db: Session, source_url: str) -> DiscoveredProduct | None:
    return db.scalar(
        select(DiscoveredProduct).where(DiscoveredProduct.source_url == source_url)
    )


def create(
    db: Session,
    data: dict,
    *,
    auto_score: bool = False,
    ai_fn: Callable[[dict], Any] | None = None,
) -> tuple[DiscoveredProduct, bool]:
    """商品候補を作成する。(商品, created) を返す。

    source_url が指定され、かつ既存と重複する場合は作成せず既存を返す
    （重複登録防止）。

    auto_score=True のときは、新規作成した商品を作成直後に自動スコアリングする
    （既定 False。既存を再利用した場合は再スコアリングしない）。
    """
    source_url = data.get("source_url")
    if source_url:
        existing = get_by_source_url(db, source_url)
        if existing is not None:
            return existing, False

    product = DiscoveredProduct(**data)
    db.add(product)
    db.commit()
    db.refresh(product)
    logger.info(
        "discovered product created: id=%s platform=%s score=%s",
        product.id, product.source_platform, product.overall_discovery_score,
    )
    if auto_score:
        _apply_scores(db, product, ai_fn=ai_fn)
    return product, True


def _apply_scores(
    db: Session,
    product: DiscoveredProduct,
    *,
    ai_fn: Callable[[dict], Any] | None = None,
) -> DiscoveredProduct:
    """商品を評価してスコア系カラム・reasoning・next_action を更新・保存する。"""
    scores = discovery_scoring_service.score(product, ai_fn=ai_fn)
    for key, value in scores.items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return product


def score_product(
    db: Session,
    product_id: int,
    *,
    ai_fn: Callable[[dict], Any] | None = None,
) -> DiscoveredProduct | None:
    """保存済み商品を評価し、スコア系カラムを更新して返す。

    対象が存在しなければ None。AI 呼び出し（ai_fn）が失敗しても
    discovery_scoring_service 側でルールベースにフォールバックする。
    """
    product = db.get(DiscoveredProduct, product_id)
    if product is None:
        return None
    return _apply_scores(db, product, ai_fn=ai_fn)


def update(db: Session, product_id: int, updates: dict) -> DiscoveredProduct | None:
    """商品候補を更新する（渡されたフィールドのみ更新）。

    不正な source_platform / status は ValueError。
    """
    product = db.get(DiscoveredProduct, product_id)
    if product is None:
        return None

    platform = updates.get("source_platform", _UNSET)
    if platform is not _UNSET and platform is not None and platform not in _VALID_PLATFORM:
        raise ValueError(f"未知の source_platform: {platform}")
    status = updates.get("status", _UNSET)
    if status is not _UNSET and status is not None and status not in _VALID_STATUS:
        raise ValueError(f"未知の status: {status}")

    for key, value in updates.items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return product


# --- Contact Intelligence 連携（Discovery Engine v1-5） -----------------------
# 発掘元プラットフォーム → 案件（Project）の SourceSite。対応が無いものは other。
_PLATFORM_TO_SOURCE_SITE = {
    "kickstarter": SourceSite.kickstarter,
    "indiegogo": SourceSite.indiegogo,
}


def _map_platform_to_source_site(platform: str | None) -> SourceSite:
    return _PLATFORM_TO_SOURCE_SITE.get((platform or "").lower(), SourceSite.other)


def _ensure_project_for_product(
    db: Session, product: DiscoveredProduct, used_url: str
) -> "Project":
    """発掘商品に対応する案件（Project）を用意する（source_url 一致で再利用）。

    Contact Discovery は project_id（NOT NULL FK）を要求するため、既存の Contact
    Intelligence へ橋渡しする最小の Project を作る。Project.source_url は一意なので、
    同一 URL の案件が既にあれば再作成せず再利用する（二重登録防止）。
    """
    from app.models.project import Project
    from app.schemas.project import ProjectCreate
    from app.services import project_service

    src = product.source_url or used_url
    if src:
        existing = db.scalar(select(Project).where(Project.source_url == src))
        if existing is not None:
            return existing

    title = (
        product.product_name
        or product.project_title
        or product.creator_name
        or f"発掘商品 #{product.id}"
    )
    data = ProjectCreate(
        title=title[:500],
        source_site=_map_platform_to_source_site(product.source_platform),
        source_url=src,
        category=product.category,
        description=product.description,
        # company_name は creator_name を優先（探索の company/maker 名に使う）
        maker_name=product.creator_name,
        # official_website_url を優先、無ければ source_url を営業先 URL に使う
        maker_url=product.official_website_url or product.source_url,
    )
    return project_service.create_project(db, data)


def start_contact_intelligence_from_product(
    db: Session,
    product_id: int,
    *,
    discovery_fn: Callable[..., Any] | None = None,
) -> dict | None:
    """発掘商品から Contact Intelligence（連絡先探索）を開始する。

    - official_website_url があればそれを、無ければ source_url を used_url に使う。
    - 既存の Contact Discovery 起動処理（contact_discovery_service.run_discovery）を
      再利用して ContactDiscovery を作成し、その id を discovered_products.
      contact_discovery_id に保存する。
    - すでに contact_discovery_id があれば再作成せず既存 id を返す。

    Returns（router がステータスで 404/400 を判定する）:
      - 商品が無い          → None（router で 404）
      - URL が無い          → status="error"（router で 400）
      - 既存連携あり        → status="existing"
      - 新規に開始          → status="started"
    dict のキー: product_id / contact_discovery_id / used_url / status / message

    discovery_fn を注入すると run_discovery の代わりに使う（テストで実ネットワークを
    避けるため。既定は既存の contact_discovery_service.run_discovery）。
    """
    product = db.get(DiscoveredProduct, product_id)
    if product is None:
        return None

    # すでに Contact Intelligence 連携済みなら再作成しない（既存 id を返す）
    if product.contact_discovery_id is not None:
        from app.models.contact_discovery import ContactDiscovery

        existing = db.get(ContactDiscovery, product.contact_discovery_id)
        if existing is not None:
            used = (
                product.official_website_url
                or product.source_url
                or existing.official_site_url
            )
            return {
                "product_id": product.id,
                "contact_discovery_id": existing.id,
                "used_url": used,
                "status": "existing",
                "message": "既存の Contact Intelligence 調査があります。",
            }

    used_url = product.official_website_url or product.source_url
    if not used_url:
        return {
            "product_id": product.id,
            "contact_discovery_id": None,
            "used_url": None,
            "status": "error",
            "message": (
                "official_website_url も source_url も未設定のため、"
                "Contact Intelligence を開始できません。"
            ),
        }

    project = _ensure_project_for_product(db, product, used_url)

    from app.services import contact_discovery_service

    run = discovery_fn or contact_discovery_service.run_discovery
    row = run(db, project)

    product.contact_discovery_id = row.id
    db.commit()
    db.refresh(product)
    logger.info(
        "contact intelligence started from product: product=%s contact_discovery=%s",
        product.id, row.id,
    )
    return {
        "product_id": product.id,
        "contact_discovery_id": row.id,
        "used_url": used_url,
        "status": "started",
        "message": "Contact Intelligence 調査を開始しました。",
    }


def list_products(
    db: Session,
    *,
    platform: str | None = None,
    status: str | None = None,
    category: str | None = None,
    min_score: int | None = None,
    sort: str = "score",
) -> list[DiscoveredProduct]:
    """商品候補一覧を取得する（フィルター・並び替え対応）。

    sort:
      - "score"（既定）: 総合発掘スコア降順（未設定は末尾）
      - "created"      : 作成日時の新しい順
    """
    stmt = select(DiscoveredProduct)
    if platform:
        stmt = stmt.where(DiscoveredProduct.source_platform == platform)
    if status:
        stmt = stmt.where(DiscoveredProduct.status == status)
    if category:
        stmt = stmt.where(DiscoveredProduct.category == category)
    if min_score is not None:
        stmt = stmt.where(DiscoveredProduct.overall_discovery_score >= min_score)

    if sort == "created":
        stmt = stmt.order_by(
            desc(DiscoveredProduct.created_at), desc(DiscoveredProduct.id)
        )
    else:  # "score"（既定）。未設定（NULL）は末尾へ（DB 非依存の並び）。
        stmt = stmt.order_by(
            DiscoveredProduct.overall_discovery_score.is_(None),
            desc(DiscoveredProduct.overall_discovery_score),
            desc(DiscoveredProduct.id),
        )
    return list(db.scalars(stmt).all())
