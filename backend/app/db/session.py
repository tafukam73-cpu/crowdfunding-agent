"""DB エンジンとセッションの生成。"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# コネクションプール設定。重いバックグラウンドジョブがセッションを保持しても、
# 画面表示用の GET がコネクション待ちで固まらないよう十分なプールを確保する。
# SQLite（テスト）は QueuePool のサイズ引数を受け付けないため付与しない。
_pool_kwargs: dict = {}
if not settings.database_url.startswith("sqlite"):
    _pool_kwargs = {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": 1800,  # 30 分でコネクションを作り直す（切断対策）
    }

engine = create_engine(
    settings.database_url, pool_pre_ping=True, echo=False, **_pool_kwargs
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI の依存性注入で使う DB セッション。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
