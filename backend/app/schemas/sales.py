"""営業ワークフロー / 今日営業する案件 / ダッシュボードのスキーマ（pydantic v2）。"""
from __future__ import annotations

from pydantic import BaseModel

from app.models.project import SalesStatus


class WorkflowStep(BaseModel):
    key: str          # research / contact / email / dm
    label: str
    done: bool


class WorkflowChannel(BaseModel):
    key: str          # contact_form / instagram / linkedin / facebook / ... / gmail
    label: str
    url: str
    recommended: bool = False


class WorkflowOut(BaseModel):
    project_id: int
    sales_status: SalesStatus
    steps: list[WorkflowStep]
    channels: list[WorkflowChannel]
    priority_score: int
    stars: int
    ready_to_sell: bool


class SalesStatusUpdate(BaseModel):
    sales_status: SalesStatus


class TodayProject(BaseModel):
    project_id: int
    title: str
    source_site: str
    sales_status: SalesStatus
    priority_score: int
    stars: int
    reasons: list[str]


class TodayListOut(BaseModel):
    items: list[TodayProject]


class RankingItem(BaseModel):
    """AI 営業優先ランキングの 1 件（Executive Summary を統合）。"""

    project_id: int
    rank: int
    title: str
    source_site: str
    score: int
    stars: int
    sales_target: str            # "yes" / "no" / "要確認"
    recommended_channel: str
    recommended_action: str
    product_category: str
    japan_sales_status: str
    japan_distributor_status: str
    contact_status: str
    japan_market_fit: str
    reasons: list[str]
    cautions: list[str]


class RankingListOut(BaseModel):
    items: list[RankingItem]


class SalesDashboardOut(BaseModel):
    ready_count: int          # 営業準備完了
    today_count: int          # 今日営業する件数
    awaiting_reply_count: int # 返信待ち
    replied_count: int        # 返信あり
    negotiating_count: int    # 商談中
    won_count: int            # 契約数
    contacted_count: int      # 営業済み
