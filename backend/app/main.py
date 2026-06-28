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
    crm,
    email_drafts,
    email_settings,
    evaluate,
    health,
    japanese_success,
    projects,
    reply_assistant,
    sales,
    scrape,
    usage,
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
app.include_router(email_drafts.router)
app.include_router(email_settings.router)
app.include_router(japanese_success.router)
app.include_router(crm.router)
app.include_router(availability.router)
app.include_router(company_research.router)
app.include_router(contact_discovery.router)
app.include_router(reply_assistant.router)
app.include_router(sales.router)
app.include_router(usage.router)


@app.get("/")
def root() -> dict:
    return {"app": settings.app_name, "step": 5}
