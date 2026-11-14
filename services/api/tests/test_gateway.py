import asyncio
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from tta_contracts import Facets, ReasonCode, RegisterIntentRequest

from app.auth import Identity
from app.config import Settings
from app.gateway import Gateway, GatewayDenied, GatewayUnavailable
from tests.factories import DELIVERY, USER_ID


async def test_real_fastmcp_serialization_and_nested_request_signature():
    server = FastMCP("test-gateway")
    received = []

    @server.tool
    def get_facets() -> Facets:
        return Facets(claims=[], allergens=[], categories=[], temperatures=[], sales_units=[])

    @server.tool
    def register_intent(request: RegisterIntentRequest) -> dict:
        received.append(request)
        return {"accepted": True}

    tokens = []

    def factory(url, **kwargs):
        assert url == "http://nginx/mcp"
        tokens.append(kwargs["auth"])
        return Client(server)

    gateway = Gateway(Settings(agent_key="tta_agent"), client_factory=factory)
    async with gateway.session(Identity(str(USER_ID), "buyer-jwt")) as session:
        result = await session.call("get_facets", {}, Facets)
        assert result.claims == []
        await session.call(
            "register_intent",
            {
                "request_text": "milk",
                "criteria": {"query_text": "milk"},
                "quantity": 40,
                "quantity_unit": "l",
                "delivery_date": DELIVERY.isoformat(),
            },
            dict,
            actor="user",
        )
    assert tokens == ["tta_agent", "buyer-jwt"]
    assert received[0].quantity == 40


class StubClient:
    def __init__(self, failures=None):
        self.calls = []
        self.failures = failures or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def list_tools(self):
        return [
            SimpleNamespace(name=name, input_schema={"properties": {}})
            for name in (
                "get_facets",
                "place_order",
                "approve_order",
                "search_products",
            )
        ]

    async def call_tool(self, name, arguments, **kwargs):
        self.calls.append((name, arguments, kwargs))
        if self.failures:
            raise self.failures.pop(0)
        return SimpleNamespace(structured_content={"ok": True}, content=[])


async def test_agent_cannot_call_user_tool_even_before_contacting_gateway():
    client = StubClient()
    gateway = Gateway(Settings(agent_key="agent"), client_factory=lambda *a, **k: client)
    async with gateway.session(Identity(str(USER_ID), "user")) as session:
        with pytest.raises(GatewayDenied) as error:
            await session.call("approve_order", {}, dict)
    assert error.value.reason == ReasonCode.FORBIDDEN
    assert client.calls == []


async def test_read_transport_error_is_retried_with_bounded_attempts():
    client = StubClient([ConnectionError("secret"), ConnectionError("secret")])
    gateway = Gateway(Settings(agent_key="agent"), client_factory=lambda *a, **k: client)
    async with gateway.session(Identity(str(USER_ID), "user")) as session:
        assert await session.call("get_facets", {}, dict) == {"ok": True}
    assert len(client.calls) == 3


@pytest.mark.parametrize("idempotency_key,expected_attempts", [(None, 1), ("stable-request-key", 3)])
async def test_order_transport_retry_preserves_idempotency_key(idempotency_key, expected_attempts):
    client = StubClient([ConnectionError("secret")] * 3)
    gateway = Gateway(Settings(agent_key="agent"), client_factory=lambda *a, **k: client)
    arguments = {"idempotency_key": idempotency_key} if idempotency_key else {}
    async with gateway.session(Identity(str(USER_ID), "user")) as session:
        with pytest.raises(GatewayUnavailable, match="temporarily unavailable"):
            await session.call("place_order", arguments, dict)
    assert len(client.calls) == expected_attempts
    assert all(call[1] == arguments for call in client.calls)


async def test_policy_denial_is_never_retried_and_arbitrary_text_is_not_exposed():
    client = StubClient([ToolError("secret-token OVER_ORDER_LIMIT")])
    gateway = Gateway(Settings(agent_key="agent"), client_factory=lambda *a, **k: client)
    async with gateway.session(Identity(str(USER_ID), "user")) as session:
        with pytest.raises(GatewayDenied) as error:
            await session.call("place_order", {"idempotency_key": "stable"}, dict)
    assert error.value.reason == ReasonCode.OVER_ORDER_LIMIT
    assert "secret-token" not in str(error.value)
    assert len(client.calls) == 1


async def test_invalid_tool_response_is_not_retried():
    client = StubClient()
    gateway = Gateway(Settings(agent_key="agent"), client_factory=lambda *a, **k: client)
    async with gateway.session(Identity(str(USER_ID), "user")) as session:
        with pytest.raises(GatewayUnavailable, match="shared contract"):
            await session.call("get_facets", {}, Facets)
    assert len(client.calls) == 1


async def test_real_mcp_agent_reconnect_keeps_user_session_usable():
    server = FastMCP("reconnect-test")

    @server.tool
    def get_facets() -> dict:
        return {"ok": True}

    @server.tool
    def register_intent() -> dict:
        return {"ok": True}

    @server.tool
    def search_products() -> dict:
        return {"ok": True}

    failed = False
    tokens = []

    def factory(url, **kwargs):
        nonlocal failed
        tokens.append(kwargs["auth"])
        client = Client(server)
        original_call = client.call_tool

        async def call_tool(name, *args, **call_kwargs):
            nonlocal failed
            if name == "search_products" and not failed:
                failed = True
                raise ConnectionError("simulated connection loss")
            return await original_call(name, *args, **call_kwargs)

        client.call_tool = call_tool
        return client

    gateway = Gateway(Settings(agent_key="agent-key"), client_factory=factory)
    async with asyncio.timeout(5):
        async with gateway.session(Identity(str(USER_ID), "user-jwt")) as session:
            assert await session.call("get_facets", {}, dict) == {"ok": True}
            assert await session.call("register_intent", {}, dict, actor="user") == {"ok": True}
            assert await session.call("search_products", {}, dict) == {"ok": True}
            assert await session.call("register_intent", {}, dict, actor="user") == {"ok": True}
    assert tokens == ["agent-key", "user-jwt", "agent-key"]
