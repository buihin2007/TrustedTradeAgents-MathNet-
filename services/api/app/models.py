"""Buyer REST/pipeline models. Gateway models are imported from tta_contracts."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from tta_contracts import Criteria, Order, OrderOption, PricedOption

RequestStatus = Literal["running", "asking", "held", "placed", "blocked", "rejected", "failed"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class CreateRequest(StrictModel):
    text: str = Field(min_length=1, max_length=1000)


class AnswerRequest(StrictModel):
    answer: str = Field(min_length=1, max_length=1000)
    question_id: UUID


class ApprovalRequest(StrictModel):
    action: Literal["approve", "reject"]
    option_index: int = Field(default=0, ge=0, le=3)
    note: str | None = Field(default=None, max_length=500)


class ParsedRequest(StrictModel):
    criteria: Criteria
    quantity: float | None = Field(default=None, gt=0)
    quantity_unit: Literal["l", "kg", "pcs"] | None = None
    delivery_date: date | None = None


class Profile(BaseModel):
    user_id: UUID
    org_id: UUID
    agent_id: UUID
    display_name: str | None = None


class Question(BaseModel):
    id: UUID
    request_id: UUID
    question: str
    choices: list[str] = []
    answer: str | None = None
    asked_at: datetime
    answered_at: datetime | None = None


class OptionPreview(BaseModel):
    option: OrderOption
    total_eur: Decimal
    supplied_quantity: Decimal
    available: bool


class RequestRecord(BaseModel):
    id: UUID
    user_id: UUID
    org_id: UUID
    text: str
    status: RequestStatus
    step: str | None = None
    parsed: ParsedRequest | None = None
    intent_id: UUID | None = None
    order_id: UUID | None = None
    error: str | None = None
    trace_id: str | None = None
    created_at: datetime
    updated_at: datetime


class RequestDetail(RequestRecord):
    options: list[OptionPreview | PricedOption] = []
    questions: list[Question] = []
    order: Order | None = None
