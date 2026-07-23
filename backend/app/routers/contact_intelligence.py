"""Contact Intelligence v2：非同期ジョブ API。

- POST /projects/{id}/contact-intelligence/jobs      ジョブ開始（24h キャッシュ再利用）
- GET  /contact-intelligence/jobs/{job_id}           ジョブ取得（ポーリング用）
- GET  /projects/{id}/contact-intelligence/jobs/latest 最新ジョブ取得
- POST /contact-intelligence/jobs/{job_id}/cancel    ジョブ中断要求

重い探索（Web調査 / Document Reader / Search Agent / full）はジョブとして別スレッドで
実行し、HTTP はすぐ返す。UI はポーリングで進捗を取得する。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.contact_intelligence_job import ContactIntelligenceJobOut
from app.services import (
    contact_intelligence_service,
    contact_search_gate,
    product_context_service,
    product_facts_service,
    project_service,
)

router = APIRouter(tags=["contact-intelligence"])


def _out(job, from_cache: bool = False) -> ContactIntelligenceJobOut:
    dto = ContactIntelligenceJobOut.model_validate(job)
    dto.from_cache = from_cache
    return dto


@router.post(
    "/projects/{project_id}/contact-intelligence/jobs",
    response_model=ContactIntelligenceJobOut,
)
def create_job(
    project_id: int,
    job_type: str = Query(
        "full_contact_intelligence",
        description="web_research / document_reader / search_agent / "
        "full_contact_intelligence",
    ),
    force: bool = Query(False, description="24h キャッシュを無視して再実行する"),
    override_reason: str | None = Query(
        None,
        description="適性ゲート不合格でも管理者が手動実行する場合の理由（監査のため記録する）",
    ),
    db: Session = Depends(get_db),
) -> ContactIntelligenceJobOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    try:
        job, from_cache = contact_intelligence_service.create_job(
            db, project, job_type, force=force, override_reason=override_reason
        )
    except contact_search_gate.GateBlocked as blocked:
        # フロントのボタン非表示だけに頼らず、サーバー側で拒否する。
        # 理由と判定内容を返し、UI が「対象外の理由」と手動実行の導線を出せるようにする。
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "日本クラファン適性ゲートによりメール探索を開始できません",
                "gate": _gate_detail(blocked.result),
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _out(job, from_cache)


# 画面へ出さない内部フィールド（予測スコア・内部判定文）。
_INTERNAL_GATE_FIELDS = {
    "japan_crowdfunding_score",
    "japan_crowdfunding_threshold",
    "contact_search_gate_reason",
    "reasons",
    "rationale",
    "gate_checked_at",
}


def _gate_detail(result: dict) -> dict:
    """ゲート判定を API レスポンス向けに整形する。

    内部スコア（japan_crowdfunding_score など）は返さない。画面には
    user_reasons（探索しなかった具体的理由）だけを出す。
    """
    checked = result.get("gate_checked_at")
    return {
        **{k: v for k, v in result.items() if k not in _INTERNAL_GATE_FIELDS},
        "gate_checked_at": checked.isoformat() if checked else None,
    }


@router.get("/projects/{project_id}/facts")
def get_product_facts(project_id: int, db: Session = Depends(get_db)) -> dict:
    """商品ファクトシート（確認可能な事実のみ）を返す。

    予測値・可能性・適性スコアは含めない。取得できない項目は「未取得」を返し、
    各項目には取得元 URL / 取得元の種類 / 最終確認日時を付ける。
    """
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return product_facts_service.build(db, project)


@router.get("/projects/{project_id}/contact-search-gate")
def get_contact_search_gate(project_id: int, db: Session = Depends(get_db)) -> dict:
    """メール探索の可否（日本クラファン適性ゲート）と商品コンテキストを返す。

    UI はこれを見て「何の商品を調査するのか」「なぜ探索できる/できないのか」を表示する。
    """
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    gate = contact_search_gate.evaluate(db, project)
    context = product_context_service.build(db, project, gate=gate)
    return {"gate": _gate_detail(gate), "product": context}


@router.get(
    "/contact-intelligence/jobs/{job_id}", response_model=ContactIntelligenceJobOut
)
def get_job(job_id: int, db: Session = Depends(get_db)) -> ContactIntelligenceJobOut:
    job = contact_intelligence_service.get_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return _out(job)


@router.get(
    "/projects/{project_id}/contact-intelligence/jobs/latest",
    response_model=ContactIntelligenceJobOut,
)
def get_latest_job(
    project_id: int,
    job_type: str | None = Query(None),
    db: Session = Depends(get_db),
):
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    job = contact_intelligence_service.get_latest(db, project_id, job_type)
    if job is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return _out(job)


@router.post(
    "/contact-intelligence/jobs/{job_id}/cancel",
    response_model=ContactIntelligenceJobOut,
)
def cancel_job(
    job_id: int, db: Session = Depends(get_db)
) -> ContactIntelligenceJobOut:
    job = contact_intelligence_service.request_cancel(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return _out(job)
