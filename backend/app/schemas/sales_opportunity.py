"""営業案件管理 API のスキーマ（Contact Intelligence v5）。"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.sales_opportunity import SalesOpportunityStatus


class SalesOpportunityOut(BaseModel):
    id: int
    contact_discovery_id: int

    company_name: str | None = None
    product_name: str | None = None
    website_url: str | None = None

    sales_score: int | None = None
    sales_priority: str | None = None
    recommended_channel: str | None = None
    primary_email: str | None = None
    contact_form_url: str | None = None
    primary_social_url: str | None = None

    status: SalesOpportunityStatus
    next_action: str | None = None
    next_action_due_date: date | None = None
    notes: str | None = None

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SalesOpportunityUpdate(BaseModel):
    """営業案件の更新（渡されたフィールドのみ更新）。"""

    status: SalesOpportunityStatus | None = None
    next_action: str | None = None
    next_action_due_date: date | None = None
    notes: str | None = None

    @field_validator("status")
    @classmethod
    def _status_value(cls, v):
        # Enum を文字列値に落として service に渡す
        return v.value if isinstance(v, SalesOpportunityStatus) else v
