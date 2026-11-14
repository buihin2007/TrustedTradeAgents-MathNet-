from types import SimpleNamespace

import httpx
import pytest
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from tta_contracts import ReasonCode

from app.auth import Identity
from app.config import Settings
from app.gateway import Gateway, GatewayDenied, GatewayUnavailable
from tests.factories import USER_ID
from tests.test_gateway import StubClient


def http_error(status, headers=None):
    request = httpx.Request("POST", "http://gateway/mcp")
    response = httpx.Response(status, request=request, headers=headers or {})
    return httpx.HTTPStatusError("private-upstream-detail", request=request, response=response)


async def call_with(client, name="get_facets", arguments=None, actor="agent"):
    gateway = Gateway(Settings(agent_key="private-agent"), client_factory=lambda *a, **k: client)
    async with gateway.session(Identity(str(USER_ID), "private-user")) as session:
        return await session.call(name, arguments or {}, dict, actor=actor)


@pytest.mark.parametrize("status", [400, 404, 422])
async def test_invalid_http_request_is_not_retried(status):
    client = StubClient([http_error(status)])
    with pytest.raises(GatewayUnavailable, match="shared tool contract") as error:
        await call_with(client)
    assert len(client.calls) == 1
    assert "private-upstream-detail" not in str(error.value)


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_is_not_retried(status):
    client = StubClient([http_error(status)])
    with pytest.raises(GatewayDenied) as error:
        await call_with(client)
    assert error.value.reason == ReasonCode.FORBIDDEN
    assert len(client.calls) == 1


async def test_transient_http_failure_closes_old_connection_before_retry():
    class TrackedClient(StubClient):
        closed = 0

        async def __aexit__(self, *args):
            self.closed += 1

        async def call_tool(self, name, arguments, **kwargs):
            if len(self.calls) == 1:
                assert self.closed == 1
            return await super().call_tool(name, arguments, **kwargs)

    client = TrackedClient([http_error(503, {"Retry-After": "0"})])
    assert await call_with(client) == {"ok": True}
    assert len(client.calls) == 2
    assert client.closed == 2


async def test_long_retry_after_stops_instead_of_retrying_too_soon():
    client = StubClient([http_error(429, {"Retry-After": "120"})])
    with pytest.raises(GatewayUnavailable, match="retry later"):
        await call_with(client)
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        ExceptionGroup("transport", [ToolError("private-token OVER_WEEKLY_BUDGET")]),
        ExceptionGroup("transport", [http_error(403)]),
    ],
)
async def test_grouped_denial_is_not_retried_or_exposed(error):
    client = StubClient([error])
    with pytest.raises(GatewayDenied) as result:
        await call_with(client)
    assert "private-token" not in str(result.value)
    assert len(client.calls) == 1


async def test_jsonrpc_invalid_parameters_are_not_retried():
    client = StubClient([MCPError(-32602, "private-invalid-arguments")])
    with pytest.raises(GatewayUnavailable, match="shared tool contract"):
        await call_with(client)
    assert len(client.calls) == 1


async def test_jsonrpc_timeout_can_retry_read():
    client = StubClient([MCPError(-32001, "private-timeout")])
    assert await call_with(client) == {"ok": True}
    assert len(client.calls) == 2


async def test_unknown_programming_error_is_not_retried():
    client = StubClient([RuntimeError("private-programming-detail")])
    with pytest.raises(GatewayUnavailable, match="operation failed"):
        await call_with(client)
    assert len(client.calls) == 1


async def test_approval_transport_failure_is_never_automatically_retried():
    client = StubClient([TimeoutError("private-timeout")])
    with pytest.raises(GatewayUnavailable):
        await call_with(client, "approve_order", {"order_id": "order", "option_index": 0}, actor="user")
    assert len(client.calls) == 1


async def test_empty_structured_object_is_a_valid_result():
    class EmptyClient(StubClient):
        async def call_tool(self, *args, **kwargs):
            return SimpleNamespace(structured_content={}, content=[])

    assert await call_with(EmptyClient()) == {}


async def test_wrapped_connection_initialization_timeout_can_retry():
    error = RuntimeError("Failed to initialize server session")
    error.__cause__ = TimeoutError("private-timeout")
    client = StubClient([error])
    assert await call_with(client) == {"ok": True}
    assert len(client.calls) == 2
