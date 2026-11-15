"""Typed MCP client with isolated agent/user credentials and bounded retries."""

import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from fastmcp import Client
from fastmcp.exceptions import ToolError
from opentelemetry import trace
from opentelemetry.propagate import inject
from pydantic import TypeAdapter, ValidationError
from tta_contracts import ReasonCode
from tta_contracts.tools import allowed

from .auth import Identity
from .config import Settings
from .telemetry import io

tracer = trace.get_tracer("tta.api")


class GatewayUnavailable(Exception):
    """Transport/protocol failure; no upstream credentials or messages escape."""


class GatewayDenied(Exception):
    def __init__(self, reason: ReasonCode | None = None):
        self.reason = reason
        super().__init__("Gateway denied the operation")


def error_leaves(error):
    """HTTP transports may deliver failures inside an AnyIO exception group."""
    if isinstance(error, BaseExceptionGroup):
        return [leaf for child in error.exceptions for leaf in error_leaves(child)]
    if isinstance(error, RuntimeError) and error.__cause__ is not None:
        return error_leaves(error.__cause__)
    return [error]


def classify_failure(error, attempt):
    errors = error_leaves(error)
    for item in errors:
        if isinstance(item, ToolError):
            reason = next((code for code in ReasonCode if code.value in str(item)), None)
            raise GatewayDenied(reason) from None
    statuses = [getattr(getattr(item, "response", None), "status_code", None) for item in errors]
    if any(status in {401, 403} for status in statuses):
        raise GatewayDenied(ReasonCode.FORBIDDEN) from None
    if any(status is not None and 400 <= status < 500 and status != 429 for status in statuses):
        raise GatewayUnavailable("Gateway rejected the request; check the shared tool contract") from None
    if any(getattr(item, "code", None) in {-32600, -32601, -32602} for item in errors):
        raise GatewayUnavailable("Gateway rejected the request; check the shared tool contract") from None
    transient = all(
        isinstance(item, (ConnectionError, TimeoutError, OSError, httpx.TransportError))
        or type(item).__module__.split(".")[0] in {"httpx", "httpx2", "httpcore", "httpcore2"}
        or getattr(item, "code", None) in {-32001, -32603}
        for item in errors
    )
    if not transient:
        raise GatewayUnavailable("Gateway operation failed") from None
    delay = 0.25 * (2**attempt)
    for item in errors:
        response = getattr(item, "response", None)
        if response is None or response.status_code not in {429, 503}:
            continue
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                seconds = float(retry_after)
            except ValueError:
                try:
                    seconds = (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    seconds = delay
            if seconds > 5:
                # Do not retry sooner than requested or park a background task for minutes.
                raise GatewayUnavailable("Gateway is busy; retry later after checking order status") from None
            delay = max(delay, seconds)
    return delay


class Gateway:
    def __init__(self, settings: Settings, client_factory=Client):
        self.settings = settings
        self.client_factory = client_factory

    @asynccontextmanager
    async def session(self, user: Identity):
        async with AsyncExitStack() as stack:
            yield GatewaySession(self.settings, user, self.client_factory, stack)


class GatewaySession:
    def __init__(self, settings, user, factory, stack):
        self.settings = settings
        self.user = user
        self.factory = factory
        self.stack = stack
        self.clients = {}
        self.schemas = {}
        self.contexts = {}

    async def call(self, tool: str, arguments: dict, result_type, *, actor="agent"):
        with tracer.start_as_current_span(f"mcp.{tool}", record_exception=False) as span:
            io(span, input=arguments, **{"mcp.tool": tool, "mcp.actor": actor})
            try:
                result = await self._call(tool, arguments, result_type, actor=actor)
            except GatewayDenied as exc:
                io(span, output={"denied": exc.reason.value if exc.reason else None})
                raise
            except GatewayUnavailable as exc:
                io(span, output={"unavailable": str(exc)})
                raise
            data = TypeAdapter(result_type).dump_python(result, mode="json")
            text = json.dumps(data, ensure_ascii=False, default=str)
            io(span, output=text if len(text) <= 20000 else text[:19000] + "…")
            return result

    async def _call(self, tool: str, arguments: dict, result_type, *, actor="agent"):
        if not allowed(tool, actor):
            raise GatewayDenied(ReasonCode.FORBIDDEN)
        token = self.user.token if actor == "user" else self.settings.agent_key
        if not token:
            raise GatewayUnavailable("Gateway credentials are not configured")
        # place_order is retried only because its contract requires an idempotency key.
        attempts = (
            3
            if tool
            in {
                "get_facets",
                "search_products",
                "get_price_availability",
                "get_substitutes",
                "get_order",
            }
            or (tool == "place_order" and arguments.get("idempotency_key"))
            else 1
        )
        # A batch search does up to 30 searches in one call: give it room and do not repeat it on a timeout
        # (a retry would redo the same slow work). Everything else answers well within 20 s.
        timeout = {"search_products_batch": 40, "place_order": 30}.get(tool, 20)
        if tool == "search_products_batch":
            attempts = 1
        for attempt in range(attempts):
            try:
                if actor not in self.clients:
                    context = await self.stack.enter_async_context(AsyncExitStack())
                    self.contexts[actor] = context
                    client = await context.enter_async_context(
                        self.factory(self.settings.mcp_url, auth=token, timeout=45)
                    )
                    tools = await client.list_tools()
                    self.clients[actor] = client
                    self.schemas[actor] = {
                        t.name: (getattr(t, "input_schema", None) or getattr(t, "inputSchema", {})) for t in tools
                    }
                client = self.clients[actor]
                if tool not in self.schemas[actor]:
                    raise GatewayUnavailable(f"Required MCP tool is not available: {tool}")
                schema = self.schemas[actor][tool]
                properties = schema.get("properties", {})
                # Accommodate the two Pydantic tool signatures without changing contracts:
                # tool(request: Model) or tool(**model_fields). Schema decides the shape.
                payload = {"request": arguments} if set(properties) == {"request"} else arguments
                metadata = {}
                inject(metadata)
                result = await client.call_tool(tool, payload, meta=metadata, timeout=timeout)
                data = result.structured_content
                if data is None:
                    blocks = [part.text for part in result.content if hasattr(part, "text")]
                    if len(blocks) != 1:
                        raise GatewayUnavailable("Gateway response has no structured result")
                    data = json.loads(blocks[0])
                if isinstance(data, dict) and set(data) == {"result"}:
                    data = data["result"]
                return TypeAdapter(result_type).validate_python(data)
            except ToolError as exc:
                # Only expose enumerated policy reasons, never arbitrary gateway text.
                reason = next((code for code in ReasonCode if code.value in str(exc)), None)
                raise GatewayDenied(reason) from None
            except (ValidationError, ValueError, TypeError):
                raise GatewayUnavailable("Gateway response does not match the shared contract") from None
            except GatewayUnavailable:
                raise
            except Exception as exc:
                delay = classify_failure(exc, attempt)
                self.clients.pop(actor, None)
                context = self.contexts.pop(actor, None)
                if context:
                    try:
                        await context.aclose()
                    except Exception:
                        pass  # preserve the classified error; old sessions must not affect a retry
                if attempt + 1 == attempts:
                    if tool == "search_products_batch":
                        raise GatewayUnavailable(
                            "The Valio Aimo catalog search took too long. Try again, or ask for fewer dishes."
                        ) from None
                    raise GatewayUnavailable("Gateway is temporarily unavailable") from None
                await asyncio.sleep(delay)
        raise GatewayUnavailable("Gateway is temporarily unavailable")
