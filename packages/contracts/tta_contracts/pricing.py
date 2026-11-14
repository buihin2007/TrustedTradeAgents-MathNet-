"""get_price_availability and get_substitutes."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .limits import MAX_PRICE_IDS, MAX_SUBSTITUTE_IDS
from .search import ProductHit


class PriceAvailabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent_id: str
    product_ids: list[int] = Field(min_length=1, max_length=MAX_PRICE_IDS)
    delivery_date: date


class ScheduleLine(BaseModel):
    available_amount: float          # Terminal model returns 999999 = effectively unlimited
    available_date: date
    sales_unit: str
    sales_block: str | None = None


class Offer(BaseModel):
    product_id: int
    unit_price_eur: Decimal | None            # price of ONE sales unit, excl. tax
    unit_price_with_tax_eur: Decimal | None
    comparison_price_eur: Decimal | None      # per comparison_unit
    comparison_unit: Literal["KG", "L"] | None
    logistic_model: Literal["Stock", "Terminal"] | None
    available_on_date: float | None           # units available on delivery_date
    schedule: list[ScheduleLine] = []
    sales_block: str | None = None
    closing_time: datetime | None = None      # order cut-off for delivery_date
    error: str | None = None                  # upstream error for this product


class PriceAvailabilityResult(BaseModel):
    quote_id: str                  # pass to place_order; gateway compares prices against it
    delivery_date: date
    quoted_at: datetime
    offers: list[Offer]


class SubstitutesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent_id: str
    product_ids: list[int] = Field(min_length=1, max_length=MAX_SUBSTITUTE_IDS)


class SubstituteGroup(BaseModel):
    product_id: int
    substitutes: list[ProductHit]  # already filtered by the intent's must constraints
