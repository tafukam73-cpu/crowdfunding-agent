"""ヘルスチェック用ルーター。DB 疎通・AI 経路の状態も確認する。"""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    """アプリと DB の稼働状況を返す。"""
    try:
        db.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {exc}"

    return {
        "status": "ok",
        "database": db_status,
    }


@router.get("/ai/status")
def ai_status() -> dict:
    """AI 経路（mock / claude）と使用モデルを返す。

    ANTHROPIC_API_KEY の「設定有無」のみを確認する（キー本体は返さない／
    トークンも消費しない）。キーが有効かどうかは実際の評価実行で確認する。
    """
    using_claude = bool(settings.anthropic_api_key)
    return {
        "api_key_set": using_claude,
        "evaluator": "claude" if using_claude else "mock",
        "email_generator": "claude" if using_claude else "mock",
        "model": settings.anthropic_model if using_claude else None,
    }
