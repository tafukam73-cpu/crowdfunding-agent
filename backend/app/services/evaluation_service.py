"""AI 評価の業務ロジック。

評価器（モック/Claude）で案件を評価し、ai_evaluations に保存。
projects の latest_score / latest_recommendation を更新する。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import get_evaluator, pricing
from app.ai.evaluator import Evaluator
from app.config import settings
from app.db.session import SessionLocal
from app.models.evaluation import AiEvaluation
from app.models.project import Project
from app.services import usage_service

logger = logging.getLogger("evaluation")


def evaluate_project(
    db: Session, project: Project, evaluator: Evaluator | None = None
) -> AiEvaluation:
    """1 案件を評価して保存し、最新評価キャッシュを更新する。"""
    evaluator = evaluator or get_evaluator()
    result = evaluator.evaluate(project)

    # Ulule 案件は 6 つの追加スコアと理由を両エンジン共通で付与する（総合スコアは不変）。
    from app.ai import ulule as ulule_eval

    if ulule_eval.is_ulule(project):
        result.axis_scores = {
            **result.axis_scores,
            **ulule_eval.ulule_axis_scores(project),
        }
        reason = ulule_eval.reason_text(project)
        result.reasons = (result.reasons + "\n・" + reason) if result.reasons else reason

    ev = AiEvaluation(
        project_id=project.id,
        total_score=result.total_score,
        recommendation=result.recommendation.value,
        axis_scores=result.axis_scores,
        reasons=result.reasons,
        concerns=result.concerns,
        sales_comment=result.sales_comment,
        model=result.model,
    )
    db.add(ev)

    # Claude 実行時のみトークン/コストを記録（モックは last_usage=None で記録なし）
    usage_service.record_usage(
        db,
        kind="evaluation",
        model=result.model,
        usage=getattr(evaluator, "last_usage", None),
        project_id=project.id,
    )

    project.latest_score = result.total_score
    project.latest_recommendation = result.recommendation.value

    db.commit()
    db.refresh(ev)
    return ev


def list_evaluations(db: Session, project_id: int) -> list[AiEvaluation]:
    stmt = (
        select(AiEvaluation)
        .where(AiEvaluation.project_id == project_id)
        .order_by(AiEvaluation.created_at.desc())
    )
    return list(db.scalars(stmt))


def evaluate_unevaluated_background(only_unevaluated: bool = True) -> None:
    """バックグラウンド一括評価。自前セッションを使う。"""
    db = SessionLocal()
    try:
        evaluator = get_evaluator()
        stmt = select(Project)
        if only_unevaluated:
            stmt = stmt.where(Project.latest_score.is_(None))
        for project in db.scalars(stmt):
            try:
                evaluate_project(db, project, evaluator=evaluator)
            except Exception as exc:  # noqa: BLE001  1件失敗で全体を止めない
                db.rollback()
                logger.warning("evaluate failed (project=%s): %s", project.id, exc)
    finally:
        db.close()


def count_unevaluated(db: Session) -> int:
    from sqlalchemy import func

    return (
        db.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.latest_score.is_(None))
        )
        or 0
    )


def estimate_evaluation_run(db: Session) -> dict:
    """一括評価（未評価のみ）の推定トークン数・コストを返す。"""
    count = count_unevaluated(db)
    use_claude = bool(settings.anthropic_api_key)
    est_in = count * pricing.EVAL_EST_INPUT_TOKENS
    est_out = count * pricing.EVAL_EST_OUTPUT_TOKENS
    cost = pricing.cost_usd(settings.anthropic_model, est_in, est_out) if use_claude else 0.0
    return {
        "mode": "claude" if use_claude else "mock",
        "model": settings.anthropic_model if use_claude else "mock",
        "count": count,
        "est_input_tokens": est_in,
        "est_output_tokens": est_out,
        "est_cost_usd": round(cost, 6),
    }
