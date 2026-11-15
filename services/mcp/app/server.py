"""Valio Aimo's Streamable HTTP MCP gateway."""

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Annotated

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse
from tta_contracts import (
    ApproveOrderRequest,
    Facets,
    GatewayEvent,
    Intent,
    Mandate,
    MandateLimits,
    Order,
    PlaceOrderRequest,
    PriceAvailabilityRequest,
    PriceAvailabilityResult,
    RegisterIntentRequest,
    RejectOrderRequest,
    SearchProductsBatchRequest,
    SearchProductsRequest,
    SearchResult,
    SetAgentStatusRequest,
    SubstituteGroup,
    SubstitutesRequest,
)
from tta_contracts import (
    ReasonCode as Reason,
)

from search.catalog import Catalog, substitutes

from . import orders
from .auth import SupabaseJWT
from .db import all_rows, json_model, mandate_for, one, order_for, uuid
from .gate import Gate, current_call
from .openrouter import OpenRouter
from .policy import in_scope
from .valio import ValioClient
from tta_contracts.telemetry import langfuse_otel_env

DEFAULT_LIMITS = MandateLimits()


@dataclass
class Runtime:
    pool: AsyncConnectionPool
    verifier: SupabaseJWT
    router: OpenRouter
    valio: ValioClient
    catalog: Catalog


@asynccontextmanager
async def gateway_runtime():
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required")
    # Psycopg's async driver requires the selector loop on Windows.
    async with (
        AsyncConnectionPool(
            dsn,
            min_size=0,
            max_size=2,
            timeout=5,
            open=False,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": 4,
                "options": "-c statement_timeout=5000 -c lock_timeout=4000",
            },
        ) as pool,
        httpx.AsyncClient(timeout=5, follow_redirects=False) as http,
    ):
        router = OpenRouter(
            http,
            os.getenv("OPENROUTER_API_KEY", ""),
            os.getenv("JEV_MODEL") or "~typesafe/jev-latest",
            os.getenv("LLM_MODEL", ""),
            os.getenv("EMBED_MODEL") or "baai/bge-m3",
        )
        yield Runtime(
            pool=pool,
            router=router,
            catalog=Catalog(router),
            valio=ValioClient(
                http,
                os.getenv("VALIO_API_BASE", ""),
                os.getenv("VALIO_API_KEY", ""),
                os.getenv("VALIO_CUSTOMER", ""),
            ),
            verifier=SupabaseJWT(
                os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL", ""),
                os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", ""),
                http,
            ),
        )


def create_server(runtime_factory=gateway_runtime) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_server):
        async with runtime_factory() as runtime:
            yield {"runtime": runtime}

    mcp = FastMCP(
        "Valio Aimo gateway",
        lifespan=lifespan,
        middleware=[Gate()],
        mask_error_details=True,
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "mcp"})

    @mcp.tool
    async def register_intent(request: RegisterIntentRequest) -> Intent:
        """Register a buyer's original request and hard criteria for 24 hours."""
        state = current_call.get()
        if (
            not isfinite(request.quantity)
            or not request.request_text.strip()
            or "\x00" in request.request_text
        ):
            raise ToolError("Invalid intent")
        await state.catalog.validate(state.conn, request.criteria)
        row = await one(
            state.conn,
            """
            insert into wholesaler.intents
                (org_id, user_id, request_text, criteria, quantity, quantity_unit, delivery_date)
            values (%s, %s, %s, %s, %s, %s, %s)
            returning id::text as intent_id, org_id::text, user_id::text, request_text,
                      criteria, quantity, quantity_unit, delivery_date, created_at, expires_at
            """,
            (
                state.actor.org_id,
                state.actor.id,
                request.request_text,
                json_model(request.criteria),
                request.quantity,
                request.quantity_unit,
                request.delivery_date,
            ),
        )
        intent = Intent.model_validate(row)
        state.intent_id = uuid(intent.intent_id)
        return intent

    @mcp.tool
    async def get_facets() -> Facets:
        """Read the Valio catalog's supported claims, allergens, categories, temperatures and units."""
        state = current_call.get()
        return await state.catalog.facets(state.conn)

    @mcp.tool
    async def search_products(request: SearchProductsRequest) -> SearchResult:
        """Find catalog products for an active intent using structured filters and hybrid rank."""
        state = current_call.get()
        result, state.sql = await state.catalog.search(
            state.conn, request.criteria, state.intent.criteria.must
        )
        result.hits = [hit for hit in result.hits if in_scope(hit, state.intent, state.mandate)]
        state.row_count = len(result.hits)
        await state.touch({hit.product_id for hit in result.hits})
        result.flags = [reason.value for reason in state.reasons]
        return result

    @mcp.tool
    async def search_products_batch(request: SearchProductsBatchRequest) -> list[SearchResult]:
        """Run up to 30 catalog searches for one intent in a single call; one result per query, same order."""
        state = current_call.get()
        results, state.sql = await state.catalog.search_batch(
            state.conn, request.queries, state.intent.criteria.must
        )
        touched = set()
        for result in results:
            result.hits = [hit for hit in result.hits if in_scope(hit, state.intent, state.mandate)]
            touched |= {hit.product_id for hit in result.hits}
        state.row_count = len(touched)
        await state.touch(touched)
        for result in results:
            result.flags = [reason.value for reason in state.reasons]
        return results

    @mcp.tool
    async def get_price_availability(request: PriceAvailabilityRequest) -> PriceAvailabilityResult:
        """Get Valio EUR prices and stock for up to 50 products on the intent's delivery date."""
        state = current_call.get()
        offers = await state.valio.offers(
            list(dict.fromkeys(request.product_ids)), request.delivery_date
        )
        row = await one(
            state.conn,
            """
            insert into wholesaler.quotes (intent_id, delivery_date, offers)
            values (%s, %s, %s) returning id::text as quote_id, delivery_date, quoted_at
            """,
            (
                state.intent_id,
                request.delivery_date,
                Jsonb([offer.model_dump(mode="json") for offer in offers]),
            ),
        )
        return PriceAvailabilityResult(**row, offers=offers)

    @mcp.tool
    async def get_substitutes(request: SubstitutesRequest) -> list[SubstituteGroup]:
        """Get mapped substitutes for up to 10 products, filtered against the intent and mandate."""
        state = current_call.get()
        groups = {product_id: [] for product_id in request.product_ids}
        if state.mandate.allow_substitution:
            for source, hit in await substitutes(state.conn, list(groups)):
                if in_scope(hit, state.intent, state.mandate):
                    groups[source].append(hit)
        await state.touch({hit.product_id for hits in groups.values() for hit in hits})
        return [
            SubstituteGroup(product_id=source, substitutes=hits) for source, hits in groups.items()
        ]

    @mcp.tool
    async def place_order(request: PlaceOrderRequest) -> Order:
        """Reprice up to four options and place, hold or block the preferred option."""
        state = current_call.get()
        return state.record_order(await orders.place(state, request))

    @mcp.tool
    async def get_order(order_id: str) -> Order:
        """Read an order belonging to the caller's organization."""
        state = current_call.get()
        order, _, _ = await order_for(state.conn, state.actor.org_id, uuid(order_id))
        state.intent_id = uuid(order.intent_id)
        return order

    @mcp.tool
    async def approve_order(
        order_id: str,
        option_index: Annotated[int, Field(ge=0)],
        expected_total_eur: Annotated[Decimal, Field(ge=0)] | None = None,
    ) -> Order:
        """Reprice and approve one option of a held order as a mandate signer."""
        state = current_call.get()
        request = ApproveOrderRequest(order_id=order_id, option_index=option_index)
        return state.record_order(await orders.approve(state, request, expected_total_eur))

    @mcp.tool
    async def reject_order(request: RejectOrderRequest) -> Order:
        """Reject a held order as a mandate signer."""
        state = current_call.get()
        return state.record_order(await orders.reject(state, request))

    @mcp.tool
    async def get_mandate(agent_id: str) -> Mandate:
        """Read the current mandate for an agent in the buyer's organization."""
        state = current_call.get()
        return await mandate_for(state.conn, state.actor.org_id, uuid(agent_id))

    @mcp.tool
    async def update_mandate(
        agent_id: str,
        auto_limit_eur: Annotated[Decimal, Field(ge=0)] = DEFAULT_LIMITS.auto_limit_eur,
        order_limit_eur: Annotated[Decimal, Field(ge=0)] = DEFAULT_LIMITS.order_limit_eur,
        weekly_budget_eur: Annotated[Decimal, Field(ge=0)] = DEFAULT_LIMITS.weekly_budget_eur,
        price_tolerance_pct: Annotated[Decimal, Field(ge=0)] = DEFAULT_LIMITS.price_tolerance_pct,
        allow_substitution: bool = DEFAULT_LIMITS.allow_substitution,
        allowed_category_codes: list[str] | None = None,
    ) -> Mandate:
        """Sign and activate a new mandate version for an agent in the buyer's organization."""
        state = current_call.get()
        conn, actor = state.conn, state.actor
        agent = uuid(agent_id)
        current = await mandate_for(conn, actor.org_id, agent)
        limits = MandateLimits(
            auto_limit_eur=auto_limit_eur,
            order_limit_eur=order_limit_eur,
            weekly_budget_eur=weekly_budget_eur,
            price_tolerance_pct=price_tolerance_pct,
            allow_substitution=allow_substitution,
            allowed_category_codes=allowed_category_codes,
        )
        if not (
            0
            <= limits.auto_limit_eur
            <= limits.order_limit_eur
            <= limits.weekly_budget_eur
            <= 1_000_000_000
            and 0 <= limits.price_tolerance_pct <= 100
        ):
            raise ToolError("Invalid mandate limits")
        categories = limits.allowed_category_codes
        if categories is not None:
            valid = {entry.code for entry in (await state.catalog.facets(conn)).categories}
            if len(categories) > 500 or not set(categories).issubset(valid):
                raise ToolError("Invalid mandate categories")
        await conn.execute(
            "update wholesaler.mandates set is_current = false where id = %s",
            (uuid(current.mandate_id),),
        )
        await conn.execute(
            """
            insert into wholesaler.mandates
                (org_id, agent_id, version, auto_limit_eur, order_limit_eur, weekly_budget_eur,
                 price_tolerance_pct, allow_substitution, allowed_category_codes, signed_by)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                actor.org_id,
                agent,
                current.version + 1,
                limits.auto_limit_eur,
                limits.order_limit_eur,
                limits.weekly_budget_eur,
                limits.price_tolerance_pct,
                limits.allow_substitution,
                categories,
                actor.id,
            ),
        )
        return await mandate_for(conn, actor.org_id, agent)

    @mcp.tool
    async def set_agent_status(request: SetAgentStatusRequest) -> SetAgentStatusRequest:
        """Pause or resume an agent belonging to the buyer's organization."""
        state = current_call.get()
        row = await one(
            state.conn,
            """
            update wholesaler.agents set status = %s where org_id = %s and id = %s returning id
            """,
            (request.status, state.actor.org_id, uuid(request.agent_id)),
        )
        if row is None:
            raise ToolError(Reason.FORBIDDEN)
        return request

    @mcp.tool
    async def list_gateway_events(
        limit: Annotated[int, Field(ge=1, le=100)] = 100,
        before_id: Annotated[int, Field(gt=0, le=9_223_372_036_854_775_807)] | None = None,
    ) -> list[GatewayEvent]:
        """Read the buyer's audit events newest first. Pass the final id to get the next page."""
        state = current_call.get()
        rows = await all_rows(
            state.conn,
            """
            select id, ts, tool, actor_kind, actor_id, org_id::text, intent_id::text,
                   decision, reasons, jev_scores, sql, row_count, latency_ms, trace_id, span_id
            from wholesaler.gateway_events where org_id = %s
              and (%s::bigint is null or id < %s) order by id desc limit %s
            """,
            (state.actor.org_id, before_id, before_id, limit),
        )
        return [GatewayEvent.model_validate(row) for row in rows]

    return mcp


mcp = create_server()


def configure_tracing() -> None:
    langfuse_otel_env()  # OTEL_EXPORTER_OTLP_* from LANGFUSE_PUBLIC_KEY / SECRET_KEY / BASE_URL
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", "tta-mcp"),
            }
        )
    )
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") and os.getenv("OTEL_EXPORTER_OTLP_HEADERS"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(timeout=5)))
    trace.set_tracer_provider(provider)


if __name__ == "__main__":
    import asyncio
    import sys

    configure_tracing()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    mcp.run(transport="http", host="0.0.0.0", port=9000, path="/mcp", stateless_http=True)
