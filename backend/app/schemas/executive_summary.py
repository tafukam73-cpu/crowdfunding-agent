"""AI Executive Summary API のスキーマ。"""
from __future__ import annotations

from pydantic import BaseModel


class ExecutiveSummaryOut(BaseModel):
    project_id: int
    # 営業価値スコア（0〜100）と星評価（1〜5）
    score: int
    stars: int
    # 営業対象： "yes" / "no" / "要確認"
    sales_target: str
    # 推奨アクション（今すぐ営業 / 連絡先探索が必要 / 日本販売状況を確認 /
    # 営業対象外の可能性 / 後回し）
    recommended_action: str
    # 推奨チャネル（email / contact_form / instagram / linkedin / facebook / manual_search）
    recommended_channel: str
    product_category: str
    japan_sales_status: str
    japan_distributor_status: str
    contact_status: str
    japan_market_fit: str
    # Contact Hunter（担当者発見）
    contact_person_found: bool = False
    contact_person_name: str | None = None
    contact_person_title: str | None = None
    contact_person_department: str | None = None
    contact_person_priority: int | None = None
    reasons: list[str]
    cautions: list[str]
