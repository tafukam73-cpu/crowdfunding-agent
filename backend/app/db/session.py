"""DB エンジンとセッションの生成。"""
import os
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import db_safety
from app.config import settings

# テスト文脈で本番 DB を指したままエンジンを生成しようとした場合、接続前に停止する。
# 本番実行（TESTING 未設定・pytest 非実行）では no-op。config.py と二重の防御。
db_safety.guard_or_abort(settings.database_url)

# このプロセスの種別（pg_stat_activity.application_name に出す。API/worker/job の
# 接続を区別して idle in transaction の発生源を特定できるようにする）。
# run_single_job / worker は import 前に APP_COMPONENT を設定する。
_APP_COMPONENT = os.environ.get("APP_COMPONENT", "cfagent-api")

# コネクションプール設定。重いバックグラウンドジョブがセッションを保持しても、
# 画面表示用の GET がコネクション待ちで固まらないよう十分なプールを確保する。
# SQLite（テスト）は QueuePool のサイズ引数を受け付けないため付与しない。
_pool_kwargs: dict = {}
_connect_args: dict = {}
if not settings.database_url.startswith("sqlite"):
    _pool_kwargs = {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": 1800,  # 30 分でコネクションを作り直す（切断対策）
    }
    # 安全網（二次的対策）：主対策はアプリ側で外部処理前にトランザクションを閉じる
    # こと。それでもワーカー強制終了などで「idle in transaction」接続が残った場合に
    # 備え、PostgreSQL 側で 10 分アイドルのトランザクション接続を自動終了する。
    # 正常処理は数秒〜で commit/rollback するため 10 分では誤発火しない。
    # application_name で接続の発生源（api/worker/job）を可視化する。
    _connect_args = {
        "options": (
            "-c idle_in_transaction_session_timeout=600000 "
            f"-c application_name={_APP_COMPONENT}"
        )
    }

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False,
    connect_args=_connect_args,
    **_pool_kwargs,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI の依存性注入で使う DB セッション。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def worker_session() -> Generator[Session, None, None]:
    """CI ワーカー／サブプロセス用の短命セッション。

    `expire_on_commit=False`：commit 後も ORM のロード済みカラム値を保持するため、
    「短時間 read → commit（トランザクション解放＝接続をプールへ返却）→ 外部処理」の
    間に ORM の値へアクセスしても lazy load（＝外部処理中の DB アクセス）が発生しない。

    使い方は必ず短命に：read か write を行ったら速やかに commit/rollback して抜ける。
    外部 HTTP / Playwright / Claude 実行中はこのセッションを開いたままにしない。
    """
    db = SessionLocal()
    db.expire_on_commit = False
    try:
        yield db
    finally:
        db.close()
