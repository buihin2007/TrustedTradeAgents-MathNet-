"""FastMCP middleware for authorization, hard limits, Jev and durable audit."""

import os
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic
from uuid import UUID

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware
from fastmcp.telemetry import get_tracer
from psycopg import Error as DatabaseError
from psycopg.types.json import Jsonb
from psycopg_pool import PoolTimeout
from pydantic import ValidationError
from tta_contracts import (
    JEV_READ_TOOLS,
    JEV_WRITE_TOOLS,
    GateDecision,
    Intent,
    Mandate,
    Order,
    OrderStatus,
    PlaceOrderRequest,
    PriceAvailabilityRequest,
    RegisterIntentRequest,
    RejectOrderRequest,
    SearchProductsRequest,
    SetAgentStatusRequest,
    SubstitutesRequest,
    allowed,
)
from tta_contracts import (
    ReasonCode as Reason,
)
from tta_contracts.limits import MAX_DISTINCT_PER_INTENT

from .auth import Actor, authenticate
from .db import load_intent, mandate_for, one, products_by_id, uuid
from .openrouter import MODEL_ERRORS, jev_reason
from .orders import order_ids, previous_order, quote_prices
from .policy import in_scope
from .valio import ValioUnavailable

logger = logging.getLogger(__name__)
tracer = get_tracer()
REQUESTS = {
    "register_intent": RegisterIntentRequest,
    "search_products": SearchProductsRequest,
    "get_price_availability": PriceAvailabilityRequest,
    "get_substitutes": SubstitutesRequest,
    "place_order": PlaceOrderRequest,
    "reject_order": RejectOrderRequest,
    "set_agent_status": SetAgentStatusRequest,
}
SIGNER_TOOLS = {
    "register_intent",
    "approve_order",
    "reject_order",
    "update_mandate",
    "set_agent_status",
}
MAX_AGENT_CALLS_PER_MINUTE = 60


@dataclass
class Call:
    runtime: object
    conn: object
    actor: Actor | None = None
    intent: Intent | None = None
    intent_id: UUID | None = None
    mandate: Mandate | None = None
    replay: Order | None = None
    reference_prices: dict = field(default_factory=dict)
    touched: set[int] = field(default_factory=set)
    scores: dict[str, float] | None = None
    reasons: list[Reason] = field(default_factory=list)
    decision: GateDecision = GateDecision.ALLOW
    sql: str | None = None
    row_count: int | None = None
    trace_id: str | None = None
    span_id: str | None = None

    @property
    def valio(self):
        return self.runtime.valio

    @property
    def catalog(self):
        return self.runtime.catalog

    async def touch(self, ids: set[int]) -> None:
        if len(self.touched | ids) > MAX_DISTINCT_PER_INTENT:
            raise ToolError(Reason.CAP_EXCEEDED)
        if ids - self.touched:
            self.touched |= ids
            await self.conn.execute(
                """
                update wholesaler.intents set touched_product_ids = %s where id = %s
                """,
                (sorted(self.touched), self.intent_id),
            )

    def record_order(self, order: Order) -> Order:
        self.intent_id = uuid(order.intent_id)
        self.reasons = order.reasons
        self.decision = {
            OrderStatus.HELD: GateDecision.ASK_HUMAN,
            OrderStatus.BLOCKED: GateDecision.BLOCK,
        }.get(order.status, GateDecision.ALLOW)
        return order


current_call: ContextVar[Call] = ContextVar("current_call")


async def validate_call(state: Call, tool: str, args: dict) -> None:
    model = REQUESTS.get(tool)
    if model is None:
        return
    try:
        request = model.model_validate(args.get("request"))
    except ValidationError as exc:
        if any(error["type"] == "too_long" for error in exc.errors(include_input=False)):
            raise ToolError(Reason.CAP_EXCEEDED) from None
        raise ToolError("Invalid tool arguments") from None

    if tool not in JEV_READ_TOOLS | JEV_WRITE_TOOLS:
        return
    conn, actor = state.conn, state.actor
    state.intent_id = uuid(request.intent_id, Reason.INTENT_INVALID)
    if tool == "place_order":
        state.replay = await previous_order(conn, actor, request)
        if state.replay is not None:
            return
    state.intent, touched = await load_intent(conn, actor.org_id, state.intent_id)
    state.touched = set(touched)
    if getattr(request, "delivery_date", state.intent.delivery_date) != state.intent.delivery_date:
        raise ToolError(Reason.INTENT_INVALID)

    if isinstance(request, SearchProductsRequest):
        await state.catalog.validate(conn, request.criteria)
        return

    ids = (
        order_ids(request.options)
        if isinstance(request, PlaceOrderRequest)
        else set(request.product_ids)
    )
    if any(not 0 < product_id <= 2_147_483_647 for product_id in ids):
        raise ToolError(Reason.MUST_VIOLATED)
    await state.touch(ids)
    products = await products_by_id(conn, ids)
    if ids != products.keys():
        raise ToolError(Reason.MUST_VIOLATED)
    if not isinstance(request, PlaceOrderRequest) and any(
        not in_scope(product, state.intent, state.mandate) for product in products.values()
    ):
        raise ToolError(Reason.MUST_VIOLATED)
    if isinstance(request, PlaceOrderRequest):
        state.reference_prices = await quote_prices(state, request)


async def judge(state: Call, tool: str, args: dict) -> None:
    # Jev screens the buyer's first message in the buyer API (spam / small talk). Per-call judging
    # in the gateway is opt-in: deterministic policy (mandate, intent scope, caps) still applies.
    if os.getenv("GATEWAY_JEV") != "1":
        return
    if tool not in JEV_READ_TOOLS | JEV_WRITE_TOOLS or state.replay is not None:
        return
    previous = await one(
        state.conn,
        """
        select count(*) as calls from wholesaler.gateway_events
        where actor_kind = 'agent' and actor_id = %s and intent_id = %s
        """,
        (str(state.actor.id), state.intent_id),
    )
    parsed = REQUESTS[tool].model_validate(args["request"]).model_dump(mode="json")
    with tracer.start_as_current_span("gate.jev", record_exception=False):
        try:
            state.scores = await state.runtime.router.judge(
                tool,
                {
                    "original_request": state.intent.request_text,
                    "criteria": state.intent.criteria.model_dump(mode="json"),
                    "quantity": state.intent.quantity,
                    "quantity_unit": state.intent.quantity_unit,
                    "delivery_date": state.intent.delivery_date.isoformat(),
                    "mandate": state.mandate.model_dump(
                        mode="json",
                        exclude={
                            "signed_by",
                            "signed_at",
                            "org_id",
                            "agent_id",
                            "mandate_id",
                        },
                    ),
                    "tool": tool,
                    "arguments": parsed,
                    "previous_calls": previous["calls"],
                },
            )
            reason = jev_reason(state.scores)
        except MODEL_ERRORS:
            reason = Reason.JEV_UNAVAILABLE
    if reason == Reason.JEV_BLOCK and tool in JEV_READ_TOOLS:
        raise ToolError(reason)
    if reason:
        state.reasons.append(reason)


async def audit(state: Call, tool: str, start: float) -> None:
    actor = state.actor
    await state.conn.execute(
        """
        insert into wholesaler.gateway_events
            (tool, actor_kind, actor_id, org_id, intent_id, decision, reasons,
             jev_scores, sql, row_count, latency_ms, trace_id, span_id)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            tool,
            actor.kind if actor else "user",
            str(actor.id) if actor else "unauthenticated",
            actor.org_id if actor else None,
            state.intent_id,
            state.decision,
            state.reasons,
            Jsonb(state.scores) if state.scores else None,
            state.sql,
            state.row_count,
            int((monotonic() - start) * 1000),
            state.trace_id,
            state.span_id,
        ),
    )


class Gate(Middleware):
    async def on_call_tool(self, context, call_next):
        tool = context.message.name
        args = context.message.arguments or {}
        start = monotonic()
        authorization = get_http_headers(include={"authorization"}).get("authorization", "")
        runtime = context.fastmcp_context.lifespan_context["runtime"]
        try:
            async with runtime.pool.connection() as conn, conn.transaction():
                return_result, error = await self._run(
                    runtime, conn, tool, args, authorization, context, call_next, start
                )
        except (DatabaseError, PoolTimeout) as exc:
            logger.error("Gateway database failure (%s)", type(exc).__name__)
            raise ToolError("Gateway database unavailable") from None
        if error is not None:
            raise error from None
        return return_result

    async def _run(self, runtime, conn, tool, args, authorization, context, call_next, start):
        with get_tracer().start_as_current_span("gate.call", record_exception=False) as span:
            state = Call(runtime, conn)
            trace = span.get_span_context()
            if trace.is_valid:
                state.trace_id = f"{trace.trace_id:032x}"
                state.span_id = f"{trace.span_id:016x}"
            token = current_call.set(state)
            result = error = None
            try:
                async with conn.transaction():
                    with tracer.start_as_current_span("gate.auth", record_exception=False):
                        state.actor = await authenticate(conn, authorization, runtime.verifier)
                        actor = state.actor
                        if not allowed(tool, actor.kind) or (
                            tool in SIGNER_TOOLS and actor.role != "signer"
                        ):
                            raise ToolError(Reason.FORBIDDEN)
                        # All operations that consume budget, change mandates or touch intents serialize
                        # on the same organization row across gateway workers.
                        await conn.execute(
                            "select id from wholesaler.orgs where id = %s for update",
                            (actor.org_id,),
                        )
                        if actor.kind == "agent":
                            agent = await one(
                                conn,
                                """
                                select status from wholesaler.agents where id = %s and org_id = %s
                                """,
                                (actor.id, actor.org_id),
                            )
                            if agent is None:
                                raise ToolError(Reason.FORBIDDEN)
                            if agent["status"] == "paused":
                                raise ToolError(Reason.AGENT_PAUSED)
                            state.mandate = await mandate_for(conn, actor.org_id, actor.id)
                        else:
                            membership = await one(
                                conn,
                                """
                                select role from wholesaler.org_members where user_id = %s and org_id = %s
                                """,
                                (actor.id, actor.org_id),
                            )
                            if membership is None or (
                                tool in SIGNER_TOOLS and membership["role"] != "signer"
                            ):
                                raise ToolError(Reason.FORBIDDEN)
                async with conn.transaction():
                    with tracer.start_as_current_span("gate.caps", record_exception=False):
                        await self._check_rate(state)
                        await validate_call(state, tool, args)
                    await judge(state, tool, args)
                    result = await call_next(context)
            except ToolError as exc:
                error = exc
            except ValioUnavailable:
                error = ToolError(Reason.PARTIAL_AVAILABILITY)
            except Exception as exc:  # noqa: BLE001 - sanitize and audit unexpected tool errors
                logger.error("Gateway tool failed (%s)", type(exc).__name__)
                error = ToolError("Gateway tool unavailable")
            finally:
                current_call.reset(token)
            if error is not None:
                state.decision = GateDecision.BLOCK
                if str(error) in Reason._value2member_map_:
                    state.reasons = list(dict.fromkeys([*state.reasons, Reason(str(error))]))
            span.set_attribute("gate.tool", tool)
            span.set_attribute("gate.decision", state.decision.value)
            span.set_attribute("gate.reasons", [reason.value for reason in state.reasons])
            if state.intent_id:
                span.set_attribute("gate.intent_id", str(state.intent_id))
            if state.actor and state.actor.kind == "agent":
                span.set_attribute("gate.agent_id", str(state.actor.id))
            if state.row_count is not None:
                span.set_attribute("search.row_count", state.row_count)
            for name, score in (state.scores or {}).items():
                span.set_attribute(f"jev.{name}", score)
            await audit(state, tool, start)
            return result, error

    async def _check_rate(self, state: Call) -> None:
        if state.actor.kind != "agent":
            return
        recent = await one(
            state.conn,
            """
            select count(*) as calls from wholesaler.gateway_events
            where org_id = %s and actor_kind = 'agent' and actor_id = %s
              and ts >= now() - interval '1 minute' and not ('RATE_LIMIT' = any(reasons))
            """,
            (state.actor.org_id, str(state.actor.id)),
        )
        if recent["calls"] >= MAX_AGENT_CALLS_PER_MINUTE:
            raise ToolError(Reason.RATE_LIMIT)
