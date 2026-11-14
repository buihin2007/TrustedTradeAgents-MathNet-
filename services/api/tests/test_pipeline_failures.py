import asyncio
from uuid import UUID

from app.auth import Identity
from app.gateway import GatewayUnavailable
from app.pipeline import Pipeline, Tasks
from tests.factories import ORDER_ID, PROFILE, USER_ID, FakeGateway, FakeLLM, MemoryStore


async def test_audit_failure_after_confirmed_order_keeps_gateway_result():
    class AuditOutageStore(MemoryStore):
        async def append_event(self, *args, **kwargs):
            kind = kwargs.get("event_type", args[2] if len(args) > 2 else None)
            if kind == "order.placed":
                raise RuntimeError("private-database-detail")
            return await super().append_event(*args, **kwargs)

    store = AuditOutageStore()
    item = await store.create_request(PROFILE, "milk")
    gateway = FakeGateway()
    await Pipeline(store, gateway, FakeLLM()).run(item.id, PROFILE, Identity(str(USER_ID), "private-jwt"))
    result = store.requests[item.id]
    assert result.status == "placed"
    assert result.step == "sync_required"
    assert result.order_id == UUID(ORDER_ID)
    assert "private-database-detail" not in result.model_dump_json()
    assert len([call for call in gateway.calls if call[0] == "place_order"]) == 1


async def test_submission_timeout_marks_unknown_outcome_without_resubmitting_pipeline():
    store = MemoryStore()
    item = await store.create_request(PROFILE, "milk")
    gateway = FakeGateway(place_order=GatewayUnavailable("private-upstream-detail"))
    pipeline = Pipeline(store, gateway, FakeLLM())
    identity = Identity(str(USER_ID), "private-jwt")
    await pipeline.run(item.id, PROFILE, identity)
    result = store.requests[item.id]
    assert result.status == "failed"
    assert result.step == "submission_unknown"
    assert "Check order status" in result.error
    assert "private-upstream-detail" not in result.error
    await pipeline.run(item.id, PROFILE, identity)
    assert len([call for call in gateway.calls if call[0] == "place_order"]) == 1


async def test_shutdown_cancels_joins_task_and_persists_interruption_before_submission():
    started = asyncio.Event()

    class WaitingLLM(FakeLLM):
        async def parse(self, *args):
            started.set()
            await asyncio.Event().wait()

    store = MemoryStore()
    item = await store.create_request(PROFILE, "milk")
    gateway = FakeGateway()
    tasks = Tasks()
    tasks.start(
        Pipeline(store, gateway, WaitingLLM()).run(
            item.id,
            PROFILE,
            Identity(str(USER_ID), "private-jwt"),
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    await tasks.close()
    await asyncio.sleep(0)
    assert tasks.pending == set()
    assert store.requests[item.id].status == "failed"
    assert store.requests[item.id].step == "interrupted"
    assert "place_order" not in [call[0] for call in gateway.calls]


def test_polling_can_reconcile_confirmed_order_and_append_audit(api):
    client, _, store, _ = api
    item = client.portal.call(store.create_request, PROFILE, "milk")
    store.requests[item.id].status = "placed"
    store.requests[item.id].step = "sync_required"
    store.requests[item.id].error = "Local persistence needs reconciliation"
    store.requests[item.id].order_id = UUID(ORDER_ID)
    from tests.factories import INTENT_ID

    store.requests[item.id].intent_id = UUID(INTENT_ID)
    response = client.get(f"/api/requests/{item.id}")
    assert response.status_code == 200
    assert response.json()["status"] == "placed"
    assert response.json()["step"] == "complete"
    assert response.json()["error"] is None
    assert store.events[-1].event_type == "order.placed"
    assert client.get(f"/api/requests/{item.id}").status_code == 200
    assert len(store.events) == 1
