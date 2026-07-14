"""Contact Intelligence の非同期ジョブモデル。

AI Web調査 / Document Reader / Search Agent は重く、HTTP リクエスト中に完了させると
タイムアウトする。これらをジョブ化し、進捗・ログ・結果を DB に保存してポーリングで
取得できるようにする。ジョブは別スレッドで実行され、この行を更新していく。
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CIJobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    # ハードタイムアウト超過でワーカーが実行プロセスツリーごと強制終了した状態。
    # 単なる失敗（failed）と区別して UI に「処理停止」を明示する。
    timed_out = "timed_out"


class CIJobType(str, enum.Enum):
    web_research = "web_research"
    document_reader = "document_reader"
    search_agent = "search_agent"
    # Contact Intelligence v3：公式サイト内の再帰クロール（発見率強化）
    recursive_crawl = "recursive_crawl"
    # 個別の重い探索もジョブ化して同期POSTでの画面待ち（12秒タイムアウト）を無くす。
    # 自動抽出（recursive crawl を含む）/ v2（人手順フロー）/ AI連絡先リサーチ。
    contact_discovery = "contact_discovery"
    contact_discovery_v2 = "contact_discovery_v2"
    ai_research = "ai_research"
    full_contact_intelligence = "full_contact_intelligence"
    # Zeczec 詳細補完（メーカー名/カテゴリ/説明/公式サイト候補）。Playwright で詳細
    # ページを取得するため重い。同期 POST の画面待ちを避けてジョブ化する。
    zeczec_enrichment = "zeczec_enrichment"
    # 日本販売状況チェック（検索・AI 調査）。重いのでジョブ化し、完了後に営業適性
    # アセスメントを再計算する（独占販売可能性スコアの confidence を上げる）。
    japan_sales_check = "japan_sales_check"
    # Wadiz 手動/ブラウザ取り込み後の営業適性再評価。confirm のレスポンス後に
    # 非同期で実行する（confirm を同期で重くしないため）。ルールベースで外部HTTPなし。
    wadiz_contact_reassessment = "wadiz_contact_reassessment"
    # 営業実行パイプライン：4 言語の営業メール下書き生成。外部 Claude 呼び出しを
    # 含みうるため背景ジョブ化し、同期 POST での画面待ちを避ける。
    outreach_generation = "outreach_generation"
    # 送信後フォローアップメール生成（sales_outreach）。既存の初回生成と同じく
    # 背景ジョブ化し、同一案件の生成ジョブ重複を禁止する（同期 POST を重くしない）。
    followup_generation = "followup_generation"


class ContactIntelligenceJob(Base):
    __tablename__ = "contact_intelligence_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CIJobStatus.queued.value, index=True
    )
    # 0〜100 の進捗
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 現在の処理内容（"Web Research 実行中" 等）
    current_step: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # 進捗ログ [{ts, message}]
    logs_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 完了時の結果サマリ
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- 専用ワーカープロセス（cfagent-ci-worker）での実行管理 ---
    # ジョブを claim したワーカー識別子。二重実行防止と所有権確認に使う。
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # claim ごとに発行する一意トークン。サブプロセスはこのトークンを提示して自分が
    # 現在の実行主体であることを確認してから結果を書き込む（stale 実行の書き込み防止）。
    execution_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ワーカーの生存更新時刻。古ければ（またはワーカー死亡時）stale として回収する。
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 中断要求フラグ。API が true にし、ワーカーが検知して実行プロセスを終了させる。
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=func.false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
