"""Transactional order persistence and signer decisions."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastmcp.exceptions import ToolError
from fastmcp.telemetry import get_tracer
from psycopg.types.json import Jsonb
from tta_contracts import (
    ApproveOrderRequest,
    Offer,
    Order,
    OrderOption,
    OrderStatus,
    PlaceOrderRequest,
    RejectOrderRequest,
)
from tta_contracts import (
    ReasonCode as Reason,
)

from .db import (
    ORDER_COLUMNS,
    all_rows,
    load_intent,
    mandate_for,
    one,
    order_for,
    products_by_id,
    uuid,
)
from .policy import BLOCK_REASONS, evaluate_option, order_status


def order_ids(options: list[OrderOption]) -> set[int]:
    return {
        product_id
        for option in options
        for line in option.lines
        for product_id in (line.product_id, line.substitute_for)
        if product_id is not None
    }


async def previous_order(conn, actor, request: PlaceOrderRequest) -> Order | None:
    row = await one(
        conn,
        f"""
        select {ORDER_COLUMNS}, quote_id, preferred_index
        from wholesaler.orders where agent_id = %s and idempotency_key = %s
        """,
        (actor.id, request.idempotency_key),
    )
    if row is None:
        return None
    stored = Order.model_validate(row)
    quote_id = uuid(request.quote_id) if request.quote_id else None
    same_options = [
        (
            option.label,
            option.explanation,
            [(line.product_id, line.quantity_units, line.substitute_for) for line in option.lines],
        )
        for option in request.options
    ] == [
        (
            option.label,
            option.explanation,
            [(line.product_id, line.quantity_units, line.substitute_for) for line in option.lines],
        )
        for option in stored.options
    ]
    if (
        uuid(stored.intent_id) != uuid(request.intent_id)
        or stored.delivery_date != request.delivery_date
        or quote_id != row["quote_id"]
        or request.preferred_index != row["preferred_index"]
        or not same_options
    ):
        raise ToolError("Idempotency key already used for a different order")
    return stored


QUOTE_TTL = timedelta(minutes=15)


async def quote_prices(state, request: PlaceOrderRequest) -> dict[int, Decimal]:
    if not request.quote_id:
        return {}
    row = await one(
        state.conn,
        """
        select offers, quoted_at from wholesaler.quotes
        where id = %s and intent_id = %s and delivery_date = %s
        """,
        (uuid(request.quote_id), uuid(state.intent.intent_id), request.delivery_date),
    )
    if row is None:
        raise ToolError(Reason.FORBIDDEN)
    if datetime.now(UTC) - row["quoted_at"] > QUOTE_TTL:
        return {}  # an expired quote is no reference: every line gets PRICE_MOVED, a person decides
    offers = [Offer.model_validate(offer) for offer in row["offers"]]
    return {
        offer.product_id: offer.unit_price_eur
        for offer in offers
        if offer.unit_price_eur is not None and not offer.error
    }


async def price_options(state, options, intent, mandate, reference_prices, *, strict=False):
    conn = state.conn
    ids = order_ids(options)
    products = await products_by_id(conn, ids)
    buy_ids = {line.product_id for option in options for line in option.lines}
    usable_ids = sorted(buy_ids & products.keys())
    offers = await state.valio.offers(usable_ids, intent.delivery_date) if usable_ids else []
    # Refuse an incomplete priced snapshot. A signer must see all the requested lines and their prices.
    if not strict and any(offer.error or offer.unit_price_eur is None for offer in offers):
        raise ToolError(Reason.PARTIAL_AVAILABILITY)
    pairs = await all_rows(
        conn,
        """
        select product_id, substitute_id from wholesaler.product_substitutions
        where product_id = any(%s) and substitute_id = any(%s)
        """,
        (sorted(ids), sorted(ids)),
    )
    weekly = await one(
        conn,
        """
        select coalesce(sum(total_eur), 0) as spend from wholesaler.orders
        where org_id = %s and status = 'placed'
          and coalesce(decided_at, created_at) >= now() - interval '7 days'
        """,
        (uuid(intent.org_id),),
    )
    indexed = {offer.product_id: offer for offer in offers}
    substitutions = {(row["product_id"], row["substitute_id"]) for row in pairs}
    with get_tracer().start_as_current_span("gate.policy"):
        return [
            evaluate_option(
                option,
                intent,
                mandate,
                products,
                indexed,
                reference_prices,
                weekly["spend"],
                substitutions,
            )
            for option in options
        ]


def serialized_options(options):
    return Jsonb([option.model_dump(mode="json") for option in options])


async def store_lines(conn, order: Order) -> None:
    async with conn.cursor() as cursor:
        await cursor.executemany(
            """
            insert into wholesaler.order_lines
                (order_id, line_no, product_id, quantity_units, unit_price_eur, line_total_eur, substitute_for)
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    uuid(order.order_id),
                    i,
                    line.product_id,
                    line.quantity_units,
                    line.unit_price_eur,
                    line.line_total_eur,
                    line.substitute_for,
                )
                for i, line in enumerate(order.options[order.chosen_index].lines, start=1)
            ],
        )


async def place(state, request: PlaceOrderRequest) -> Order:
    if state.replay is not None:
        return state.replay
    priced = await price_options(
        state,
        request.options,
        state.intent,
        state.mandate,
        state.reference_prices,
    )
    preferred = priced[request.preferred_index]
    reasons = sorted(set(preferred.reasons + state.reasons))
    status = order_status(set(reasons))
    chosen = request.preferred_index if status == OrderStatus.PLACED else None
    row = await one(
        state.conn,
        f"""
        insert into wholesaler.orders
            (org_id, agent_id, intent_id, quote_id, status, reasons, options, chosen_index,
             total_eur, delivery_date, idempotency_key, trace_id, preferred_index)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        returning {ORDER_COLUMNS}
        """,
        (
            state.actor.org_id,
            state.actor.id,
            uuid(state.intent.intent_id),
            uuid(request.quote_id) if request.quote_id else None,
            status,
            reasons,
            serialized_options(priced),
            chosen,
            preferred.total_eur if chosen is not None else None,
            request.delivery_date,
            request.idempotency_key,
            state.trace_id,
            request.preferred_index,
        ),
    )
    order = Order.model_validate(row)
    if chosen is not None:
        await store_lines(state.conn, order)
    return order


async def approve(state, request: ApproveOrderRequest, expected_total_eur: Decimal | None) -> Order:
    conn, actor = state.conn, state.actor
    order, agent_id, _ = await order_for(conn, actor.org_id, uuid(request.order_id))
    state.intent_id = uuid(order.intent_id)
    if (
        order.status == OrderStatus.PLACED
        and order.decided_by == str(actor.id)
        and order.chosen_index == request.option_index
    ):
        return order
    if order.status != OrderStatus.HELD or request.option_index >= len(order.options):
        raise ToolError(Reason.FORBIDDEN)
    mandate = await mandate_for(conn, actor.org_id, agent_id)
    intent, _ = await load_intent(conn, actor.org_id, state.intent_id)
    old = order.options[request.option_index]
    if not old.lines:
        raise ToolError(Reason.PARTIAL_AVAILABILITY)
    option = OrderOption(
        label=old.label,
        explanation=old.explanation,
        lines=[
            {
                "product_id": line.product_id,
                "quantity_units": line.quantity_units,
                "substitute_for": line.substitute_for,
            }
            for line in old.lines
        ],
    )
    prices = {line.product_id: line.unit_price_eur for line in old.lines}
    [current] = await price_options(state, [option], intent, mandate, prices, strict=True)
    if len(current.lines) != len(old.lines):
        raise ToolError(Reason.PARTIAL_AVAILABILITY)
    reasons = set(current.reasons)
    needs_review = reasons & (BLOCK_REASONS | {Reason.PRICE_MOVED, Reason.PARTIAL_AVAILABILITY})
    if Reason.PRICE_MOVED in old.reasons and expected_total_eur != old.total_eur:
        needs_review.add(Reason.PRICE_MOVED)
        current.reasons = sorted(set(current.reasons) | {Reason.PRICE_MOVED})
    if expected_total_eur is not None and current.total_eur != expected_total_eur:
        needs_review.add(Reason.PRICE_MOVED)
        current.reasons = sorted(set(current.reasons) | {Reason.PRICE_MOVED})
    options = list(order.options)
    options[request.option_index] = current
    if needs_review:
        row = await one(
            conn,
            f"""
            update wholesaler.orders set options = %s, reasons = %s
            where id = %s returning {ORDER_COLUMNS}
            """,
            (serialized_options(options), sorted(needs_review), uuid(order.order_id)),
        )
        return Order.model_validate(row)
    row = await one(
        conn,
        f"""
        update wholesaler.orders
        set status = 'placed', options = %s, chosen_index = %s, total_eur = %s,
            decided_at = now(), decided_by = %s, reasons = '{{}}'
        where id = %s returning {ORDER_COLUMNS}
        """,
        (
            serialized_options(options),
            request.option_index,
            current.total_eur,
            actor.id,
            uuid(order.order_id),
        ),
    )
    result = Order.model_validate(row)
    await store_lines(conn, result)
    return result


async def reject(state, request: RejectOrderRequest) -> Order:
    conn, actor = state.conn, state.actor
    order, _, _ = await order_for(conn, actor.org_id, uuid(request.order_id))
    state.intent_id = uuid(order.intent_id)
    if order.status == OrderStatus.REJECTED and order.decided_by == str(actor.id):
        return order
    if order.status != OrderStatus.HELD:
        raise ToolError(Reason.FORBIDDEN)
    row = await one(
        conn,
        f"""
        update wholesaler.orders
        set status = 'rejected', decided_at = now(), decided_by = %s
        where id = %s returning {ORDER_COLUMNS}
        """,
        (actor.id, uuid(order.order_id)),
    )
    return Order.model_validate(row)
