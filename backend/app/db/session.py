"""DB エンジンとセッションの生成。"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, echo=False)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI の依存性注入で使う DB セッション。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
