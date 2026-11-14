from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from tta_contracts import Mandate, PricedLine, PricedOption, ReasonCode

from app.auth import Identity, current_user
from app.models import Profile
from tests.factories import AGENT_ID, INTENT_ID, ORDER_ID, ORG_ID, PROFILE, order


def held_order(api):
    client, _, store, gateway = api
    item = client.portal.call(store.create_request, PROFILE, "milk")
    store.requests[item.id].status = "held"
    store.requests[item.id].intent_id = UUID(INTENT_ID)
    store.requests[item.id].order_id = UUID(ORDER_ID)
    priced = PricedOption(
        label="best_match",
        total_eur="48",
        lines=[
            PricedLine(
                product_id=1,
                name_en="Milk",
                quantity_units=40,
                unit_price_eur="1.20",
                line_total_eur="48",
                available_units=100,
            )
        ],
    )
    gateway.responses["get_order"] = order("held", options=[priced])
    gateway.responses["approve_order"] = order("placed", options=[priced])
    gateway.responses["reject_order"] = order("rejected", options=[priced])
    return item


@pytest.mark.parametrize(
    "action,status,tool,event",
    [
        ("approve", "placed", "approve_order", "user.approved"),
        ("reject", "rejected", "reject_order", "user.rejected"),
    ],
)
def test_buyer_decision_is_forwarded_using_only_user_identity(api, action, status, tool, event):
    item = held_order(api)
    client, _, store, gateway = api
    response = client.post(f"/api/approvals/{ORDER_ID}", json={"action": action, "note": "buyer note"})
    assert response.status_code == 200
    assert response.json()["status"] == status
    assert store.requests[item.id].status == status
    assert gateway.calls[-1][0] == tool
    assert all(actor == "user" for _, _, actor in gateway.calls)
    assert store.events[-1].event_type == event
    assert client.post(f"/api/approvals/{ORDER_ID}", json={"action": action}).status_code == 409


def test_repricing_can_keep_order_held_after_approval(api):
    item = held_order(api)
    client, _, store, gateway = api
    gateway.responses["approve_order"] = order("held", reasons=[ReasonCode.PRICE_MOVED])
    response = client.post(f"/api/approvals/{ORDER_ID}", json={"action": "approve"})
    assert response.status_code == 200
    assert store.requests[item.id].status == "held"
    assert store.events[-1].decision == "approval_required"
    assert store.events[-1].event_type == "gate.held"


def test_invalid_option_index_is_not_sent_to_approval_tool(api):
    held_order(api)
    client, _, _, gateway = api
    assert client.post(f"/api/approvals/{ORDER_ID}", json={"action": "approve", "option_index": 3}).status_code == 422
    assert [name for name, _, _ in gateway.calls] == ["get_order"]


def test_other_buyer_cannot_approve_or_list_held_order(api):
    held_order(api)
    client, app, store, gateway = api
    other = uuid4()
    store.profiles[str(other)] = Profile(user_id=other, org_id=ORG_ID, agent_id=AGENT_ID)
    app.dependency_overrides[current_user] = lambda: Identity(str(other), "other-jwt")
    assert client.post(f"/api/approvals/{ORDER_ID}", json={"action": "approve"}).status_code == 404
    assert client.get("/api/approvals").json() == []
    assert gateway.calls == []


def test_approvals_list_reconciles_already_placed_order(api):
    item = held_order(api)
    client, _, store, gateway = api
    gateway.responses["get_order"] = order("placed")
    assert client.get("/api/approvals").json() == []
    assert store.requests[item.id].status == "placed"


def test_unrelated_gateway_order_is_rejected(api):
    held_order(api)
    client, _, _, gateway = api
    gateway.responses["get_order"] = order("held", intent_id=str(uuid4()))
    assert client.post(f"/api/approvals/{ORDER_ID}", json={"action": "approve"}).status_code == 502
    assert len(gateway.calls) == 1


def mandate():
    return Mandate(
        mandate_id=str(uuid4()),
        org_id=str(ORG_ID),
        agent_id=str(AGENT_ID),
        version=2,
        signed_by=str(PROFILE.user_id),
        signed_at=datetime.now(UTC),
    )


def test_mandate_and_agent_controls_use_profile_agent_and_user_identity(api):
    client, _, store, gateway = api
    gateway.responses.update(get_mandate=mandate(), update_mandate=mandate(), set_agent_status=lambda args: args)
    assert client.get("/api/mandate").status_code == 200
    assert client.put("/api/mandate", json={"auto_limit_eur": "150", "order_limit_eur": "600"}).status_code == 200
    assert client.post("/api/agent/pause").json()["status"] == "paused"
    assert client.post("/api/agent/resume").json()["status"] == "active"
    assert all(actor == "user" for _, _, actor in gateway.calls)
    assert all(args["agent_id"] == str(AGENT_ID) for _, args, _ in gateway.calls)
    assert [event.event_type for event in store.events] == ["mandate.updated", "agent.paused", "agent.resumed"]


@pytest.mark.parametrize(
    "limits",
    [
        {"auto_limit_eur": -1},
        {"auto_limit_eur": 700, "order_limit_eur": 600},
        {"weekly_budget_eur": -1},
        {"price_tolerance_pct": -1},
        {"auto_limit_eur": "NaN"},
        {"order_limit_eur": 600, "weekly_budget_eur": 500},
        {"price_tolerance_pct": 101},
    ],
)
def test_invalid_mandate_never_reaches_gateway(api, limits):
    client, _, _, gateway = api
    assert client.put("/api/mandate", json=limits).status_code == 422
    assert gateway.calls == []


def test_price_moved_requires_explicit_confirmation_of_displayed_total(api):
    held_order(api)
    client, _, _, gateway = api
    gateway.responses["get_order"].reasons = [ReasonCode.PRICE_MOVED]
    response = client.post(f"/api/approvals/{ORDER_ID}", json={"action": "approve"})
    assert response.status_code == 422
    assert "expected_total_eur" in response.json()["detail"]
    assert [call[0] for call in gateway.calls] == ["get_order"]


def test_displayed_price_is_forwarded_to_p2_approval_signature(api):
    held_order(api)
    client, _, store, gateway = api
    gateway.responses["get_order"].reasons = [ReasonCode.PRICE_MOVED]
    response = client.post(
        f"/api/approvals/{ORDER_ID}",
        json={
            "action": "approve",
            "option_index": 0,
            "expected_total_eur": "48.00",
        },
    )
    assert response.status_code == 200
    assert gateway.calls[-1] == (
        "approve_order",
        {
            "order_id": ORDER_ID,
            "option_index": 0,
            "expected_total_eur": "48.00",
        },
        "user",
    )
    assert store.events[-1].decision == "approved"


@pytest.mark.parametrize("total", ["NaN", "Infinity", "-1"])
def test_invalid_displayed_price_is_rejected_before_gateway(api, total):
    held_order(api)
    client, _, _, gateway = api
    assert (
        client.post(
            f"/api/approvals/{ORDER_ID}",
            json={
                "action": "approve",
                "expected_total_eur": total,
            },
        ).status_code
        == 422
    )
    assert gateway.calls == []


def test_agent_pause_requires_gateway_confirmation_of_correct_status(api):
    client, _, store, gateway = api
    gateway.responses["set_agent_status"] = {"agent_id": str(AGENT_ID), "status": "active"}
    response = client.post("/api/agent/pause")
    assert response.status_code == 502
    assert store.events == []
