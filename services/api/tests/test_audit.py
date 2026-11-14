from uuid import uuid4

from tta_contracts import AuditDecision, verify_chain

from app.auth import Identity, current_user
from app.models import Profile
from tests.factories import AGENT_ID, ORG_ID, PROFILE, MemoryStore


async def test_hash_chain_detects_tampering_with_old_summary_or_details():
    store = MemoryStore()
    for _ in range(3):
        await store.append_event(PROFILE, None, "agent.paused", "Buyer paused the agent")
    assert verify_chain(store.events) is None
    altered = [event.model_copy(deep=True) for event in store.events]
    altered[1].summary = "Changed historical decision"
    assert verify_chain(altered) == 2
    altered = [event.model_copy(deep=True) for event in store.events]
    altered[0].details["extra"] = "changed"
    assert verify_chain(altered) == 1
    assert verify_chain([store.events[0], store.events[2]]) == 3


def test_audit_is_user_scoped_filtered_and_paginated(api):
    client, app, store, _ = api
    client.portal.call(store.append_event, PROFILE, None, "agent.paused", "Buyer paused the agent")
    client.portal.call(store.append_event, PROFILE, None, "agent.resumed", "Buyer resumed the agent")
    assert len(client.get("/api/audit?limit=1&offset=1").json()) == 1
    assert client.get("/api/audit?decision=blocked").json() == []
    assert len(client.get(f"/api/audit?decision={AuditDecision.NONE}").json()) == 2
    other = uuid4()
    store.profiles[str(other)] = Profile(user_id=other, org_id=ORG_ID, agent_id=AGENT_ID)
    app.dependency_overrides[current_user] = lambda: Identity(str(other), "other-jwt")
    assert client.get("/api/audit").json() == []


def test_unknown_audit_filters_are_rejected(api):
    client, _, _, _ = api
    assert client.get("/api/audit?decision=made-up").status_code == 422
    assert client.get("/api/audit?limit=1001").status_code == 422
    assert client.get("/api/audit?offset=-1").status_code == 422
