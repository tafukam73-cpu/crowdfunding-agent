"""営業ワークフロー / 今日営業する案件 / 営業ダッシュボード API。

- GET   /projects/{id}/workflow        営業ワークフロー（ステップ・チャネル・優先順位）
- PATCH /projects/{id}/sales-status     営業状況を更新（CRM へ営業履歴を自動記録）
- GET   /sales/today                    今日営業すべき案件（準備完了・未営業）
- GET   /sales/dashboard                営業ダッシュボードの集計
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.project import ProjectOut
from app.schemas.sales import (
    CopilotCard,
    CopilotDashboardOut,
    OutreachGenerateIn,
    OutreachGenerateOut,
    OutreachOut,
    RankingListOut,
    SalesDashboardOut,
    SalesStatusUpdate,
    TodayListOut,
    TodayPriorityOut,
    TodayTasksOut,
    WorkflowOut,
)
from app.services import (
    project_service,
    sales_assessment_service,
    sales_copilot_service,
    sales_copilot_v2_service,
    sales_outreach_service,
    workflow_service,
)

router = APIRouter(tags=["sales"])


@router.get("/projects/{project_id}/workflow", response_model=WorkflowOut)
def get_workflow(project_id: int, db: Session = Depends(get_db)) -> WorkflowOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return workflow_service.compute_workflow(db, project)


@router.patch("/projects/{project_id}/sales-status", response_model=ProjectOut)
def update_sales_status(
    project_id: int, payload: SalesStatusUpdate, db: Session = Depends(get_db)
) -> ProjectOut:
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return project_service.update_sales_status(db, project, payload.sales_status)


@router.get("/sales/today", response_model=TodayListOut)
def sales_today(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> TodayListOut:
    return TodayListOut(items=workflow_service.today_projects(db, limit=limit))


@router.get("/sales/dashboard", response_model=SalesDashboardOut)
def sales_dashboard(db: Session = Depends(get_db)) -> SalesDashboardOut:
    return SalesDashboardOut(**workflow_service.dashboard_summary(db))


@router.get("/sales/copilot", response_model=CopilotDashboardOut)
def sales_copilot(
    per_bucket: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
) -> CopilotDashboardOut:
    """営業 AI コパイロット・ダッシュボード。

    案件一覧・企業リサーチ・Contact Intelligence・CRM・営業状況を横断し、案件ごとに
    「今どう動くべきか（judgement）」を判断・理由付きで分類してバケットにまとめる。
    ルールベース（既存の保存済みデータのみ・Claude 非依存）。
    """
    return CopilotDashboardOut(
        **sales_copilot_service.copilot_dashboard(db, per_bucket=per_bucket)
    )


@router.get("/projects/{project_id}/copilot", response_model=CopilotCard)
def project_copilot(project_id: int, db: Session = Depends(get_db)) -> CopilotCard:
    """単一案件の営業コパイロット・カード（サマリー＋判断＋理由＋次の一手）。"""
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return CopilotCard(**sales_copilot_service.project_copilot(db, project))


@router.get("/sales/copilot-v2")
def sales_copilot_v2(
    per_bucket: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
) -> dict:
    """Sales Copilot v2（営業 AI 秘書）ダッシュボード。

    3 つの適性スコア（日本市場適性 / 独占販売可能性 / Makuake 適性）と既存の連絡先・
    メール・営業状況を統合し、v2 優先度スコアでランキングして分類する。ルールベース。
    """
    return sales_copilot_v2_service.copilot_v2_dashboard(db, per_bucket=per_bucket)


@router.get("/projects/{project_id}/copilot-v2")
def project_copilot_v2(project_id: int, db: Session = Depends(get_db)) -> dict:
    """単一案件の Sales Copilot v2 統合カード（3 スコア＋判断＋次アクション）。"""
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    return sales_copilot_v2_service.build_v2_card(db, project)


@router.post("/projects/{project_id}/sales-assessment")
def run_sales_assessment(
    project_id: int,
    auto_japan_check: bool = Query(
        True, description="日本販売状況が未実施なら背景ジョブを起動して再評価する"
    ),
    db: Session = Depends(get_db),
) -> dict:
    """営業適性アセスメント（3 スコア）を算出して保存する（ルールベース・非破壊）。

    日本販売状況が未実施なら背景ジョブを起動し、現在のデータで暫定評価を返す。
    ジョブ完了後に自動で再評価（新しい行として保存）される。重い検索は同期実行しない。
    """
    project = project_service.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    out = sales_assessment_service.assess_with_japan(
        db, project, auto_check=auto_japan_check
    )
    row = out["assessment"]
    return {
        "project_id": project.id,
        "japan_market_fit_score": row.japan_market_fit_score,
        "exclusivity_score": row.exclusivity_score,
        "makuake_fit_score": row.makuake_fit_score,
        "overall_priority_score": row.overall_priority_score,
        "confidence": row.confidence,
        "engine": row.engine,
        "provisional": out["provisional"],
        "japan_job_id": out["japan_job_id"],
        "japan_job_status": out["japan_job_status"],
        "japan_state": out["japan_state"],
        "details": row.details_json,
    }


@router.post("/sales/assessments/run-missing")
def run_missing_assessments(
    site: str | None = Query(None, description="対象サイト（例: wadiz）。未指定は全営業対象"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """未評価の営業対象案件を明示的に一括評価する（バッチ POST）。

    GET 表示中には起動しない。各案件で暫定 Assessment を保存し、日本販売チェックが
    未実施なら背景ジョブを起動する（重複ジョブは作らない）。連絡先が無くても市場
    適性/Makuake 適性は評価でき、総合を 0 点にはしない。
    """
    return sales_assessment_service.run_missing_assessments(db, site=site, limit=limit)


@router.get("/sales/tasks", response_model=TodayTasksOut)
def sales_tasks(
    per_group: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
) -> TodayTasksOut:
    """トップページ「今日やること」（営業状況で分類した案件リスト）。"""
    return TodayTasksOut(**workflow_service.today_tasks(db, per_group=per_group))


@router.get("/sales/today-priority", response_model=TodayPriorityOut)
def sales_today_priority(
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
) -> TodayPriorityOut:
    """今日営業する案件（営業実行パイプライン）。優先度順・読み取り専用。

    重み: 日本市場適性 / 独占販売可能性 / Makuake 適性 / email 取得 / maker 取得 /
    新規案件。Contact Intelligence 未完了の案件は除外する。GET 内で重い処理は行わない。
    """
    items = sales_outreach_service.today_priority(db, limit=limit)
    return TodayPriorityOut(items=items)


@router.post("/sales/outreach/generate", response_model=OutreachGenerateOut)
def generate_outreach(
    payload: OutreachGenerateIn, db: Session = Depends(get_db)
) -> OutreachGenerateOut:
    """営業メール（英語/韓国語/中国語/日本語）を背景ジョブで生成する。

    生成は sales_outreach の draft に保存し、CRM（sales_opportunities）へ反映する。
    同一案件の下書きは 1 本のみ（重複作成防止）。外部 Claude 呼び出しは POST 内では
    行わず、背景ジョブで実行する（GET/POST を同期で重くしない）。
    """
    project = project_service.get_project(db, payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="案件が見つかりません")
    out = sales_outreach_service.request_generation(db, project)
    return OutreachGenerateOut(
        outreach=OutreachOut(**sales_outreach_service.serialize(db, out["outreach"])),
        job_id=out["job_id"],
        job_status=out["job_status"],
        created=out["created"],
        duplicate=out["duplicate"],
    )


@router.get("/sales/outreach/{project_id}", response_model=OutreachOut)
def get_outreach(project_id: int, db: Session = Depends(get_db)) -> OutreachOut:
    """案件の営業アウトリーチ（下書き・状態・Gmail 送信 URL）を返す。"""
    row = sales_outreach_service.get_by_project(db, project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="営業アウトリーチがありません")
    return OutreachOut(**sales_outreach_service.serialize(db, row))


@router.get("/sales/ranking", response_model=RankingListOut)
def sales_ranking(
    limit: int = Query(20, ge=1, le=50),
    site: str | None = Query(None),
    candidates_only: bool = Query(True),
    unsold_only: bool = Query(False),
    contact_only: bool = Query(False),
    not_started_only: bool = Query(False),
    status_filter: str = Query(
        "not_started",
        description="営業状況フィルター: not_started(未営業のみ・既定) / all(すべて表示) "
        "/ awaiting_reply(返事待ち) / followup(フォローアップ対象) / negotiating(商談中)",
    ),
    ulule_only: bool = Query(False),
    sort: str = Query("score"),
    db: Session = Depends(get_db),
) -> RankingListOut:
    """AI 営業優先ランキング（Executive Summary を統合・スコア順）。

    既定（status_filter=not_started）では未営業の案件だけを返し、営業アクション済み
    （営業済み・返信待ち・商談中 等）は除外する。
    """
    items = workflow_service.ranking(
        db,
        limit=limit,
        site=site,
        candidates_only=candidates_only,
        unsold_only=unsold_only,
        contact_only=contact_only,
        not_started_only=not_started_only,
        status_filter=status_filter,
        ulule_only=ulule_only,
        sort=sort,
    )
    return RankingListOut(items=items)
