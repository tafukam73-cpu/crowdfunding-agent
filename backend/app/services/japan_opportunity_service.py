"""日本市場機会 分析の業務ロジック（Japan Opportunity Engine v1-2）。

発掘商品（discovered_products）に対する日本市場評価の分析結果を保存・取得する
土台。v1-2 では CRUD のみで、ルールベース評価・AI 評価・実検索は後続で実装する。

- スコアは 0〜100 に正規化（範囲外は丸め、非数値は None）。
- evidence_json は dict / list のみ安全に保存（それ以外は None）。
- 1 発掘商品に複数分析を持てる（最新を取得できる）。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.discovered_product import DiscoveredProduct
from app.models.japan_opportunity_analysis import JapanOpportunityAnalysis
from app.schemas.japan_opportunity import SCORE_FIELDS

logger = logging.getLogger("japan_opportunity")

_VALID_SORT = {"score_desc", "created_desc"}


def _clamp_score(value: Any) -> int | None:
    """0〜100 の整数に正規化する。None はそのまま、非数値は None（安全側）。"""
    if value is None:
        return None
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, v))


def _safe_evidence(value: Any) -> Any:
    """evidence_json は dict / list のみ受け入れる（それ以外は None）。"""
    if isinstance(value, (dict, list)):
        return value
    return None


def _normalize(data: dict) -> dict:
    """入力 dict のスコアを正規化し、evidence_json を安全化した新しい dict を返す。"""
    out = dict(data)
    for field in SCORE_FIELDS:
        if field in out:
            out[field] = _clamp_score(out[field])
    if "evidence_json" in out:
        out["evidence_json"] = _safe_evidence(out["evidence_json"])
    return out


def create_analysis(
    db: Session, discovered_product_id: int, data: dict
) -> JapanOpportunityAnalysis:
    """分析を作成する。発掘商品が存在しなければ ValueError。

    data には discovered_product_id を含めない（引数で受け取る）。含まれていても
    引数の値を優先する。
    """
    product = db.get(DiscoveredProduct, discovered_product_id)
    if product is None:
        raise ValueError(f"発掘商品が見つかりません: id={discovered_product_id}")

    payload = _normalize(data)
    payload.pop("discovered_product_id", None)
    analysis = JapanOpportunityAnalysis(
        discovered_product_id=discovered_product_id, **payload
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    logger.info(
        "japan opportunity analysis created: id=%s product=%s score=%s",
        analysis.id, discovered_product_id, analysis.overall_opportunity_score,
    )
    return analysis


def get_analysis(db: Session, analysis_id: int) -> JapanOpportunityAnalysis | None:
    return db.get(JapanOpportunityAnalysis, analysis_id)


def get_latest_for_product(
    db: Session, discovered_product_id: int
) -> JapanOpportunityAnalysis | None:
    """発掘商品の最新分析（作成日時が新しいもの）を返す。無ければ None。"""
    stmt = (
        select(JapanOpportunityAnalysis)
        .where(
            JapanOpportunityAnalysis.discovered_product_id == discovered_product_id
        )
        .order_by(
            desc(JapanOpportunityAnalysis.created_at),
            desc(JapanOpportunityAnalysis.id),
        )
        .limit(1)
    )
    return db.scalar(stmt)


def update_analysis(
    db: Session, analysis_id: int, updates: dict
) -> JapanOpportunityAnalysis | None:
    """分析を更新する（渡されたフィールドのみ）。対象が無ければ None。

    discovered_product_id は変更しない（渡されても無視）。
    """
    analysis = db.get(JapanOpportunityAnalysis, analysis_id)
    if analysis is None:
        return None

    payload = _normalize(updates)
    payload.pop("discovered_product_id", None)
    for key, value in payload.items():
        setattr(analysis, key, value)
    db.commit()
    db.refresh(analysis)
    return analysis


def list_analyses(
    db: Session,
    *,
    discovered_product_id: int | None = None,
    min_score: int | None = None,
    sort: str = "score_desc",
) -> list[JapanOpportunityAnalysis]:
    """分析一覧を取得する（フィルター・並び替え対応）。

    sort:
      - "score_desc"（既定）: 総合機会スコア降順（未設定は末尾）
      - "created_desc"      : 作成日時の新しい順
    """
    stmt = select(JapanOpportunityAnalysis)
    if discovered_product_id is not None:
        stmt = stmt.where(
            JapanOpportunityAnalysis.discovered_product_id == discovered_product_id
        )
    if min_score is not None:
        stmt = stmt.where(
            JapanOpportunityAnalysis.overall_opportunity_score >= min_score
        )

    if sort == "created_desc":
        stmt = stmt.order_by(
            desc(JapanOpportunityAnalysis.created_at),
            desc(JapanOpportunityAnalysis.id),
        )
    else:  # "score_desc"（既定）。未設定（NULL）は末尾へ（DB 非依存の並び）。
        stmt = stmt.order_by(
            JapanOpportunityAnalysis.overall_opportunity_score.is_(None),
            desc(JapanOpportunityAnalysis.overall_opportunity_score),
            desc(JapanOpportunityAnalysis.id),
        )
    return list(db.scalars(stmt).all())
