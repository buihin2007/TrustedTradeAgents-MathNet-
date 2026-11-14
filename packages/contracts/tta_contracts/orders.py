"""place_order, get_order, approve_order, reject_order."""
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .limits import MAX_OPTIONS


class OrderStatus(StrEnum):
    PLACED = "placed"
    HELD = "held"          # waiting for the mandate signer
    BLOCKED = "blocked"
    REJECTED = "rejected"  # held, then rejected by the signer


class ReasonCode(StrEnum):
    # policy: block
    MUST_VIOLATED = "MUST_VIOLATED"
    OVER_ORDER_LIMIT = "OVER_ORDER_LIMIT"
    OVER_WEEKLY_BUDGET = "OVER_WEEKLY_BUDGET"
    LOT_SIZE = "LOT_SIZE"
    # policy: hold
    OVER_AUTO_LIMIT = "OVER_AUTO_LIMIT"
    SUBSTITUTION = "SUBSTITUTION"
    PARTIAL_AVAILABILITY = "PARTIAL_AVAILABILITY"
    PRICE_MOVED = "PRICE_MOVED"
    # Jev
    JEV_BLOCK = "JEV_BLOCK"
    JEV_UNSURE = "JEV_UNSURE"
    JEV_UNAVAILABLE = "JEV_UNAVAILABLE"
    # gate
    FORBIDDEN = "FORBIDDEN"            # wrong identity type for this tool
    AGENT_PAUSED = "AGENT_PAUSED"
    INTENT_INVALID = "INTENT_INVALID"  # unknown, other org, or expired
    CAP_EXCEEDED = "CAP_EXCEEDED"      # ids per call / distinct products per intent
    RATE_LIMIT = "RATE_LIMIT"


OptionLabel = Literal["best_match", "cheapest", "most_reliable", "split_or_substitute"]


class OrderLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int
    quantity_units: int = Field(gt=0)        # number of sales units
    substitute_for: int | None = None        # original product_id if this line is a substitute


class OrderOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: OptionLabel
    lines: list[OrderLineIn] = Field(min_length=1, max_length=10)
    explanation: str | None = Field(default=None, max_length=500)


class PlaceOrderRequest(BaseModel):
    """Agent sends ids and quantities only. The gateway recomputes every price and total."""
    model_config = ConfigDict(extra="forbid")
    intent_id: str
    quote_id: str | None = None
    delivery_date: date
    options: list[OrderOption] = Field(min_length=1, max_length=MAX_OPTIONS)
    preferred_index: int = Field(default=0, ge=0)
    idempotency_key: str = Field(min_length=8, max_length=100)

    @model_validator(mode="after")
    def _index_in_range(self):
        if self.preferred_index >= len(self.options):
            raise ValueError("preferred_index out of range")
        return self


class PricedLine(BaseModel):
    product_id: int
    name_en: str | None
    quantity_units: int
    unit_price_eur: Decimal
    line_total_eur: Decimal
    available_units: float | None
    substitute_for: int | None = None


class PricedOption(BaseModel):
    label: OptionLabel
    lines: list[PricedLine]
    total_eur: Decimal
    explanation: str | None = None
    reasons: list[ReasonCode] = []   # why this option alone would hold or block


class Order(BaseModel):
    order_id: str
    intent_id: str
    status: OrderStatus
    reasons: list[ReasonCode] = []
    options: list[PricedOption]
    chosen_index: int | None = None  # set when placed
    total_eur: Decimal | None = None
    delivery_date: date
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None    # user id of the signer, for held orders
    trace_id: str | None = None


class ApproveOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    option_index: int = Field(ge=0)


class RejectOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str
    note: str | None = Field(default=None, max_length=500)
