"""FastAPI アプリのエントリポイント。

Step 2：データモデル拡張・Alembic・案件 CRUD API。
スキーマ管理は Alembic（`alembic upgrade head`）で行う。
起動時に projects が空ならモックデータを投入する（開発用）。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db.session import SessionLocal
from app.models import Project  # noqa: F401  （メタデータ登録のため）
from app.routers import (
    availability,
    company_research,
    contact_discovery,
    contact_intelligence,
    crm,
    email_drafts,
    email_settings,
    evaluate,
    executive_summary,
    health,
    japan_sales,
    japanese_success,
    lead_qualification,
    projects,
    reply_assistant,
    sales,
    sales_opportunities,
    scrape,
    usage,
    wadiz_browser_capture,
    wadiz_import,
)
from app.seed import seed_if_empty
from app.services import scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 開発用：空ならモックデータを投入（テーブルは Alembic で作成済み前提）
    # 日本クラファン成功事例は実スクレイピングのため起動時には収集せず、
    # 画面/API（POST /japanese-success/collect）から明示的に収集する。
    db = SessionLocal()
    try:
        seed_if_empty(db)
    except Exception:  # noqa: BLE001  マイグレーション未適用などでも起動は止めない
        db.rollback()
    # Contact Intelligence の実行は専用ワーカー（cfagent-ci-worker）が担う。API 起動
    # （--reload で頻繁に起きる）では running ジョブを潰さない（ワーカーが所有）。
    # heartbeat が途絶えた孤児（ワーカー異常終了）だけを安全に回収する。
    try:
        from app.services import contact_intelligence_service

        contact_intelligence_service.recover_stale_jobs(db)
    except Exception:  # noqa: BLE001  回収失敗でも起動は止めない
        db.rollback()
    finally:
        db.close()

    # 日次自動収集スケジューラ（有効時のみ起動）
    if settings.scrape_schedule_enabled:
        try:
            scheduler.start()
        except Exception:  # noqa: BLE001  スケジューラ失敗でもアプリは起動させる
            pass

    yield

    scheduler.shutdown()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(projects.router)
app.include_router(scrape.router)
app.include_router(evaluate.router)
app.include_router(executive_summary.router)
app.include_router(email_drafts.router)
app.include_router(email_settings.router)
app.include_router(japanese_success.router)
app.include_router(japan_sales.router)
app.include_router(crm.router)
app.include_router(availability.router)
app.include_router(company_research.router)
app.include_router(contact_discovery.router)
app.include_router(contact_intelligence.router)
app.include_router(lead_qualification.router)
app.include_router(reply_assistant.router)
app.include_router(sales.router)
app.include_router(sales_opportunities.router)
app.include_router(usage.router)
app.include_router(wadiz_import.router)
app.include_router(wadiz_browser_capture.router)


@app.get("/")
def root() -> dict:
    return {"app": settings.app_name, "step": 5}
