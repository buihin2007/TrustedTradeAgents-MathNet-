"""Typed MCP client with isolated agent/user credentials and bounded retries."""

import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager

from fastmcp import Client
from fastmcp.exceptions import ToolError
from opentelemetry.propagate import inject
from pydantic import TypeAdapter, ValidationError
from tta_contracts import ReasonCode
from tta_contracts.tools import allowed

from .auth import Identity
from .config import Settings


class GatewayUnavailable(Exception):
    """Transport/protocol failure; no upstream credentials or messages escape."""


class GatewayDenied(Exception):
    def __init__(self, reason: ReasonCode | None = None):
        self.reason = reason
        super().__init__("Gateway denied the operation")


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

    async def call(self, tool: str, arguments: dict, result_type, *, actor="agent"):
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
        for attempt in range(attempts):
            try:
                if actor not in self.clients:
                    client = await self.stack.enter_async_context(
                        self.factory(self.settings.mcp_url, auth=token, timeout=15)
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
                result = await client.call_tool(tool, payload, meta=metadata, timeout=15)
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
            except Exception:
                self.clients.pop(actor, None)
                if attempt + 1 == attempts:
                    raise GatewayUnavailable("Gateway is temporarily unavailable") from None
                await asyncio.sleep(0.25 * (2**attempt))
        raise GatewayUnavailable("Gateway is temporarily unavailable")
