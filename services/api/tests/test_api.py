import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import Identity, current_user
from app.config import Settings
from app.main import create_app
from app.models import Profile
from tests.factories import AGENT_ID, ORG_ID


def wait_for(client, request_id, *statuses):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        detail = client.get(f"/api/requests/{request_id}")
        if detail.json()["status"] in statuses:
            break
        time.sleep(0.01)
    return detail


def test_create_request_asks_for_a_basket_then_places_the_order(api):
    client, _, store, _ = api
    response = client.post("/api/requests", json={"text": "40 litres of milk"})
    assert response.status_code == 202
    request_id = response.json()["id"]
    detail = wait_for(client, request_id, "asking")
    question = detail.json()["parsed"]["feed"][-1]
    assert question["t"] == "ask" and question["field"] == "basket"
    assert [option["value"] for option in question["options"]] == ["b:0", "cancel"]
    answered = client.post(
        f"/api/requests/{request_id}/answer",
        json={"question_id": question["id"], "answer": question["options"][0]["label"], "values": ["b:0"]},
    )
    assert answered.status_code == 202
    detail = wait_for(client, request_id, "placed", "failed", "blocked")
    assert detail.status_code == 200
    assert detail.json()["status"] == "placed"
    assert detail.json()["order"]["status"] == "placed"
    assert store.events[0].event_type == "request.created"
    assert "buyer-jwt" not in detail.text


def test_unknown_user_profile_is_forbidden(api):
    client, app, _, gateway = api
    app.dependency_overrides[current_user] = lambda: Identity(str(uuid4()), "other-token")
    assert client.post("/api/requests", json={"text": "milk"}).status_code == 403
    assert [call for call in gateway.calls if call[0] != "get_facets"] == []  # start-up warm-up only


def test_other_user_cannot_read_request_even_in_same_org(api):
    client, app, store, gateway = api
    request_id = client.post("/api/requests", json={"text": "milk"}).json()["id"]
    other_id = uuid4()
    store.profiles[str(other_id)] = Profile(user_id=other_id, org_id=ORG_ID, agent_id=AGENT_ID)
    app.dependency_overrides[current_user] = lambda: Identity(str(other_id), "other-token")
    before = len(gateway.calls)
    assert client.get(f"/api/requests/{request_id}").status_code == 404
    assert client.get("/api/requests").json() == []
    assert len(gateway.calls) == before


@pytest.mark.parametrize("text", ["", "   ", "x" * 1001])
def test_invalid_request_is_not_started(api, text):
    client, _, store, gateway = api
    assert client.post("/api/requests", json={"text": text}).status_code == 422
    assert store.requests == {}
    assert [call for call in gateway.calls if call[0] != "get_facets"] == []  # start-up warm-up only


def test_business_routes_require_authentication_and_schema_is_available():
    app = create_app(Settings())
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
        for route, methods in schema["paths"].items():
            if route.startswith("/api/") and route != "/api/health":
                path = route.replace("{request_id}", str(uuid4())).replace("{order_id}", str(uuid4()))
                for method in methods:
                    response = client.request(method, path, json={})
                    assert response.status_code == 401, (method, path, response.text)
        assert "/api/requests" in schema["paths"]
        assert schema["components"]["schemas"]["CreateRequest"]["additionalProperties"] is False


def test_stream_sends_the_final_snapshot_as_server_sent_event(api):
    client, _, _, _ = api
    request_id = client.post("/api/requests", json={"text": "40 litres of milk"}).json()["id"]
    question = wait_for(client, request_id, "asking").json()["parsed"]["feed"][-1]
    client.post(f"/api/requests/{request_id}/answer", json={"question_id": question["id"], "answer": "Cancel", "values": ["cancel"]})
    wait_for(client, request_id, "rejected")
    with client.stream("GET", f"/api/requests/{request_id}/stream") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())
    assert body.startswith("data: {") and '"status": "rejected"' in body
