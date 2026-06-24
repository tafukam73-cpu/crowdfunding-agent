"""AI 評価 API。

- POST /projects/{id}/evaluate    単体評価（同期。モックは即時、Claude でも数秒）
- GET  /projects/{id}/evaluations 評価履歴
- POST /evaluate/run              未評価の一括評価（バックグラウンド）
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.evaluation import EvaluateBulkResult, EvaluationOut
from app.schemas.usage import EvaluateEstimateOut
from app.services import evaluation_service, project_service

logger = logging.getLogger("router.evaluate")

router = APIRouter(tags=["evaluation"])


@router.get("/evaluate/estimate", response_model=EvaluateEstimateOut)
def evaluate_estimate(db: Session = Depends(get_db)) -> EvaluateEstimateOut:
    """一括評価（未評価のみ）の推定トークン数・コストを返す。"""
    return evaluation_service.estimate_evaluation_run(db)


@router.post("/projects/{project_id}/evaluate", response_model=EvaluationOut)
def evaluate_project(project_id: int, db: Session = Depends(get_db)) -> EvaluationOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    try:
        return evaluation_service.evaluate_project(db, project)
    except Exception as exc:  # noqa: BLE001  失敗を記録しアプリは落とさない
        db.rollback()
        logger.warning("evaluate failed (project=%s): %s", project_id, exc)
        raise HTTPException(status_code=502, detail=f"AI評価に失敗しました: {exc}")


@router.get("/projects/{project_id}/evaluations", response_model=list[EvaluationOut])
def list_evaluations(project_id: int, db: Session = Depends(get_db)) -> list[EvaluationOut]:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return evaluation_service.list_evaluations(db, project_id)


@router.post(
    "/evaluate/run",
    response_model=EvaluateBulkResult,
    status_code=status.HTTP_202_ACCEPTED,
)
def evaluate_run(
    background_tasks: BackgroundTasks, db: Session = Depends(get_db)
) -> EvaluateBulkResult:
    """未評価の案件をバックグラウンドで一括評価する。"""
    queued = evaluation_service.count_unevaluated(db)
    background_tasks.add_task(
        evaluation_service.evaluate_unevaluated_background, True
    )
    return EvaluateBulkResult(queued=queued)
