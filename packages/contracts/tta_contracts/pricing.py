"""get_price_availability and get_substitutes."""
import json
from datetime import UTC, date, datetime
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
    valid_until: datetime | None = None   # after this the gateway holds an order built on this quote
    key_id: str | None = None             # kid of the signing key in /.well-known/jwks.json
    signature: str | None = None          # base64url Ed25519 signature over quote_signing_bytes(self)


def quote_signing_bytes(quote: "PriceAvailabilityResult") -> bytes:
    """Canonical bytes Valio Aimo signs for a quote: what was quoted, for which day, until when.

    JSON with sorted keys and no whitespace; prices as decimal strings, stock as numbers.
    Any agent can recompute these bytes from the tool result and verify the signature
    against the public key published at /.well-known/jwks.json.
    """
    body = {
        "quote_id": quote.quote_id,
        "delivery_date": quote.delivery_date.isoformat(),
        "valid_until": quote.valid_until.astimezone(UTC).isoformat() if quote.valid_until else None,
        "offers": [
            {
                "product_id": offer.product_id,
                "unit_price_eur": str(offer.unit_price_eur) if offer.unit_price_eur is not None else None,
                "available_on_date": offer.available_on_date,
                "sales_block": offer.sales_block,
            }
            for offer in sorted(quote.offers, key=lambda offer: offer.product_id)
        ],
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


class SubstitutesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent_id: str
    product_ids: list[int] = Field(min_length=1, max_length=MAX_SUBSTITUTE_IDS)


class SubstituteGroup(BaseModel):
    product_id: int
    substitutes: list[ProductHit]  # already filtered by the intent's must constraints
