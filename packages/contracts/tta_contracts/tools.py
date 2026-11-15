"""The 14 MCP tools and who may call them. Gate step 3 reads this table."""
from typing import Literal

Caller = Literal["agent", "user"]

TOOLS: dict[str, tuple[Caller, ...]] = {
    "register_intent": ("user",),
    "get_facets": ("agent",),
    "search_products": ("agent",),
    "search_products_batch": ("agent",),
    "get_price_availability": ("agent",),
    "get_substitutes": ("agent",),
    "place_order": ("agent",),
    "get_order": ("agent", "user"),
    "approve_order": ("user",),
    "reject_order": ("user",),
    "get_mandate": ("user",),
    "update_mandate": ("user",),
    "set_agent_status": ("user",),
    "list_gateway_events": ("user",),
}

# Tools that go through Jev (gate step 7). User tools stop at step 5.
JEV_READ_TOOLS = {"search_products", "search_products_batch", "get_price_availability", "get_substitutes"}
JEV_WRITE_TOOLS = {"place_order"}


def allowed(tool: str, caller: Caller) -> bool:
    return caller in TOOLS.get(tool, ())
