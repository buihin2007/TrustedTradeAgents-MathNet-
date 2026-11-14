from uuid import UUID

from fastmcp.exceptions import ToolError
from psycopg.types.json import Jsonb
from tta_contracts import Intent, Mandate, Order, ProductHit
from tta_contracts import ReasonCode as Reason

ORDER_COLUMNS = """id::text as order_id, intent_id::text, status, reasons,
    options, chosen_index, total_eur, delivery_date, created_at, decided_at,
    decided_by::text, trace_id"""


async def one(conn, sql, params=()):
    return await (await conn.execute(sql, params)).fetchone()


async def all_rows(conn, sql, params=()):
    return await (await conn.execute(sql, params)).fetchall()


def uuid(value: str, reason: Reason = Reason.FORBIDDEN) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise ToolError(reason) from None


async def load_intent(
    conn, org_id: UUID, intent_id: UUID, *, active: bool = True
) -> tuple[Intent, list[int]]:
    row = await one(
        conn,
        """
        select id::text as intent_id, org_id::text, user_id::text, request_text, criteria,
               quantity, quantity_unit, delivery_date, created_at, expires_at, touched_product_ids
        from wholesaler.intents where id = %s and org_id = %s and (%s or expires_at > now())
        """,
        (intent_id, org_id, not active),
    )
    if row is None:
        raise ToolError(Reason.INTENT_INVALID)
    return Intent.model_validate(row), row["touched_product_ids"]


async def mandate_for(conn, org_id: UUID, agent_id: UUID) -> Mandate:
    row = await one(
        conn,
        """
        select id::text as mandate_id, org_id::text, agent_id::text, signed_by::text,
               version, signed_at, auto_limit_eur, order_limit_eur, weekly_budget_eur,
               price_tolerance_pct, allow_substitution, allowed_category_codes
        from wholesaler.mandates where org_id = %s and agent_id = %s and is_current
        """,
        (org_id, agent_id),
    )
    if row is None:
        raise ToolError(Reason.FORBIDDEN)
    return Mandate.model_validate(row)


async def products_by_id(conn, ids: set[int]) -> dict[int, ProductHit]:
    rows = await all_rows(
        conn,
        """
        select * from wholesaler.v_product_search where product_id = any(%s)
        """,
        (sorted(ids),),
    )
    return {row["product_id"]: ProductHit.model_validate(row) for row in rows}


async def order_for(conn, org_id: UUID, order_id: UUID) -> tuple[Order, UUID, int]:
    row = await one(
        conn,
        f"""
        select {ORDER_COLUMNS}, agent_id, preferred_index
        from wholesaler.orders where org_id = %s and id = %s
        """,
        (org_id, order_id),
    )
    if row is None:
        raise ToolError(Reason.FORBIDDEN)
    return Order.model_validate(row), row["agent_id"], row["preferred_index"]


def json_model(model):
    return Jsonb(model.model_dump(mode="json"))
