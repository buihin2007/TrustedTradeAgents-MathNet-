from uuid import UUID

import pytest
from tta_contracts import ReasonCode, SearchResult, verify_chain

from app.auth import Identity
from app.gateway import GatewayDenied, GatewayUnavailable
from app.pipeline import Pipeline
from tests.factories import (
    ORDER_ID,
    PROFILE,
    USER_ID,
    FakeGateway,
    FakeLLM,
    MemoryStore,
    order,
    parsed,
)


async def run(gateway=None, llm=None):
    store = MemoryStore()
    item = await store.create_request(PROFILE, "40 litres of milk")
    gateway = gateway or FakeGateway()
    pipeline = Pipeline(store, gateway, llm or FakeLLM())
    await pipeline.run(item.id, PROFILE, Identity(str(USER_ID), "user-jwt"))
    return store, item.id, gateway


@pytest.mark.parametrize(
    "status,event,decision",
    [
        ("placed", "order.placed", "buy_auto"),
        ("held", "gate.held", "approval_required"),
        ("blocked", "order.blocked", "blocked"),
    ],
)
async def test_pipeline_records_gateway_decision_and_keeps_credentials_separate(status, event, decision):
    store, request_id, gateway = await run(FakeGateway(place_order=order(status)))
    result = store.requests[request_id]
    assert result.status == status
    assert result.order_id == UUID(ORDER_ID)
    assert store.events[-1].event_type == event
    assert store.events[-1].decision == decision
    assert verify_chain(store.events) is None
    assert [name for name, _, _ in gateway.calls] == [
        "get_facets",
        "register_intent",
        "search_products",
        "get_price_availability",
        "place_order",
    ]
    assert gateway.calls[1][2] == "user"
    assert all(actor == "agent" for name, _, actor in gateway.calls if name != "register_intent")
    proposal = gateway.calls[-1][1]
    assert proposal["options"][0]["lines"][0]["quantity_units"] == 40
    assert "unit_price_eur" not in proposal["options"][0]["lines"][0]
    assert proposal["idempotency_key"].startswith(str(request_id))
    assert "user-jwt" not in result.model_dump_json()


async def test_missing_quantity_asks_user_before_intent_or_purchase():
    value = parsed().model_copy(update={"quantity": None})
    store, request_id, gateway = await run(llm=FakeLLM(value))
    assert store.requests[request_id].status == "asking"
    assert store.requests[request_id].questions
    assert [name for name, _, _ in gateway.calls] == ["get_facets"]


async def test_zero_results_asks_permission_and_never_relaxes_constraints():
    empty = SearchResult(query_id="q", criteria_shape="", sql_source="template", hits=[])
    store, request_id, gateway = await run(FakeGateway(search_products=empty))
    assert store.requests[request_id].status == "asking"
    assert "get_price_availability" not in [name for name, _, _ in gateway.calls]
    assert "place_order" not in [name for name, _, _ in gateway.calls]


async def test_gate_denial_stops_pipeline_without_another_purchase_attempt():
    store, request_id, gateway = await run(
        FakeGateway(
            search_products=GatewayDenied(ReasonCode.AGENT_PAUSED),
        )
    )
    assert store.requests[request_id].status == "blocked"
    assert store.requests[request_id].error == "AGENT_PAUSED"
    assert len(gateway.calls) == 3


async def test_gateway_outage_becomes_failed_and_does_not_submit():
    store, request_id, gateway = await run(
        FakeGateway(
            get_price_availability=GatewayUnavailable("Gateway is temporarily unavailable"),
        )
    )
    assert store.requests[request_id].status == "failed"
    assert "place_order" not in [name for name, _, _ in gateway.calls]


async def test_unexpected_internal_failure_is_sanitized():
    store, request_id, _ = await run(FakeGateway(search_products=RuntimeError("secret-key")))
    assert store.requests[request_id].status == "failed"
    assert "secret-key" not in store.requests[request_id].model_dump_json()
