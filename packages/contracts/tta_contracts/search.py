"""search_products: structured criteria in, verified product hits out."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .limits import MAX_SEARCH_LIMIT


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Range(_Strict):
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _ordered(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must be <= max")
        return self


class Must(_Strict):
    """Hard constraints. The gateway never relaxes them; every returned row satisfies all of them."""
    claims_all: list[str] = []              # e.g. ["FREE_FROM_LACTOSE"], codes from get_facets
    allergens_none: list[str] = []          # allergen codes the product must NOT contain (CONTAINS or MAY_CONTAIN)
    category_codes: list[str] | None = None
    unit_volume_l: Range | None = None      # size of ONE sales unit, litres
    unit_weight_kg: Range | None = None     # size of ONE sales unit, kg
    temperature: list[str] | None = None    # temperatureCondition codes
    vendor_names: list[str] | None = None


class Prefer(_Strict):
    """Soft preferences. Only change the order of results."""
    brands: list[str] = []
    sort: Literal["relevance", "delivery_reliability", "co2"] = "relevance"


class Criteria(_Strict):
    query_text: str = Field(min_length=1, max_length=200)          # English, e.g. "lactose free milk 1 l"
    query_text_alt: str | None = Field(default=None, max_length=200)  # Finnish translation
    must: Must = Must()
    prefer: Prefer = Prefer()
    limit: int = Field(default=20, ge=1, le=MAX_SEARCH_LIMIT)

    def shape(self) -> str:
        """Cache key for generated SQL: which fields are set + sort. Values are NOT part of it."""
        keys = []
        for name, value in self.must.model_dump().items():
            if isinstance(value, dict):
                keys += [f"{name}.{k}" for k, v in value.items() if v is not None]
            elif value:
                keys.append(name)
        if self.prefer.brands:
            keys.append("prefer.brands")
        return "|".join(sorted(keys) + [f"sort={self.prefer.sort}"])

    def bind_params(self) -> dict[str, Any]:
        """Named bind parameters the generated SQL may use. :q_emb and :k are added by the executor."""
        m = self.must
        p: dict[str, Any] = {"q_text": " ".join(filter(None, [self.query_text, self.query_text_alt])),
                             "limit": self.limit}
        if m.claims_all:
            p["claims_all"] = m.claims_all
        if m.allergens_none:
            p["allergens_none"] = m.allergens_none
        if m.category_codes:
            p["category_codes"] = m.category_codes
        for field in ("unit_volume_l", "unit_weight_kg"):
            r = getattr(m, field)
            if r is not None:
                base = field.rsplit("_", 1)[0]          # unit_volume / unit_weight
                if r.min is not None:
                    p[f"{base}_min"] = r.min
                if r.max is not None:
                    p[f"{base}_max"] = r.max
        if m.temperature:
            p["temperature"] = m.temperature
        if m.vendor_names:
            p["vendor_names"] = m.vendor_names
        if self.prefer.brands:
            p["brands"] = self.prefer.brands
        return p


class SearchProductsRequest(_Strict):
    intent_id: str
    criteria: Criteria


class ProductHit(BaseModel):
    product_id: int
    name_en: str | None
    name_fi: str | None
    brand: str | None
    vendor_name: str | None
    category_code: str | None
    category_name: str | None          # LLM-derived label, see code_labels.derived
    sales_unit: str
    unit_volume_l: float | None
    unit_weight_kg: float | None
    allowed_lot_size: float | None      # order quantity must be a multiple of this; 0.01 for most products
    temperature_condition: str | None
    claims: list[str] = []
    allergens_contains: list[str] = []
    delivery_reliability: float | None   # 0..1, None = no data
    co2_kg_per_kg: float | None          # group average, None = no data
    has_substitutes: bool = False
    rrf_score: float | None = None
    matched_must: list[str] = []         # hard constraints verified in code, for explanations


class Relaxation(BaseModel):
    """Zero-result diagnosis: how many products remain if one hard constraint is dropped."""
    field: str
    count_if_dropped: int


class SearchResult(BaseModel):
    query_id: str
    criteria_shape: str
    sql_source: Literal["cache", "llm", "template", "gtin"]
    hits: list[ProductHit]
    dropped_by_postcheck: int = 0
    diagnosis: list[Relaxation] | None = None   # set only when hits is empty
    flags: list[str] = []                       # e.g. ["JEV_UNSURE"]
