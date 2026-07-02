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
