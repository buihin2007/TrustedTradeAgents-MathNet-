"""Deterministic SQL for any criteria shape. Used when there is no valid LLM SQL, and as the LLM's example."""
from tta_contracts import Criteria

# must field -> (bind params it needs, SQL condition)
CONDITIONS = {
    "claims_all": (("claims_all",), "p.claims @> cast(:claims_all as text[])"),
    "allergens_none": (("allergens_none",), "not (p.allergens_contains && cast(:allergens_none as text[]))"),
    "category_codes": (("category_codes",), "p.category_code = any(cast(:category_codes as text[]))"),
    "unit_volume_min": (("unit_volume_min",), "p.unit_volume_l >= :unit_volume_min"),
    "unit_volume_max": (("unit_volume_max",), "p.unit_volume_l <= :unit_volume_max"),
    "unit_weight_min": (("unit_weight_min",), "p.unit_weight_kg >= :unit_weight_min"),
    "unit_weight_max": (("unit_weight_max",), "p.unit_weight_kg <= :unit_weight_max"),
    "temperature": (("temperature",), "p.temperature_condition = any(cast(:temperature as text[]))"),
    "vendor_names": (("vendor_names",), "p.vendor_name = any(cast(:vendor_names as text[]))"),
}
SORT = {
    "relevance": "h.rrf_score desc",
    "delivery_reliability": "p.delivery_reliability desc nulls last, h.rrf_score desc",
    "co2": "p.co2_kg_per_kg asc nulls last, h.rrf_score desc",
}
FROM = ("from wholesaler.v_product_search p\n"
        "join wholesaler.hybrid_rank(:q_text, :q_emb, :k) h on h.product_id = p.product_id")


def must_params(params: dict) -> list[str]:
    """Bind params that carry a hard constraint (every one of them must appear in the SQL)."""
    return [k for k in CONDITIONS if k in params]


def where(params: dict, skip: str | None = None) -> str:
    conds = [CONDITIONS[k][1] for k in CONDITIONS if k in params and k != skip]
    return ("where " + "\n  and ".join(conds)) if conds else ""


def build(criteria: Criteria, params: dict) -> str:
    order = SORT[criteria.prefer.sort]
    if "brands" in params:
        order = "(p.brand = any(cast(:brands as text[]))) desc, " + order
    return f"select p.*, h.rrf_score\n{FROM}\n{where(params)}\norder by {order}\nlimit :limit"


def count(params: dict, skip: str) -> str:
    """How many products remain if one hard constraint is dropped (zero-result diagnosis)."""
    return f"select count(*) as n\n{FROM}\n{where(params, skip)}\nlimit 1"
