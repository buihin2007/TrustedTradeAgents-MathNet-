import json

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.agent import Agent
from app.auth import Identity
from app.config import Settings
from app.gateway import Gateway
from tests.factories import PROFILE, USER_ID, FakeGateway, FakeLLM, MemoryStore
from tests.test_gateway import StubClient


async def test_pipeline_spans_and_audit_share_trace_without_tokens_or_prompts():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    store = MemoryStore()
    request = await store.create_request(PROFILE, "private buyer wording")
    await Agent(store, FakeGateway(), FakeLLM(), tracer_instance=tracer).run(
        request.id,
        PROFILE,
        Identity(str(USER_ID), "private-user-jwt"),
    )
    spans = exporter.get_finished_spans()
    pipeline = next(span for span in spans if span.name == "request.pipeline")
    trace_id = f"{pipeline.context.trace_id:032x}"
    assert store.requests[request.id].trace_id == trace_id
    assert all(event.trace_id == trace_id for event in store.events)
    assert all(span.context.trace_id == pipeline.context.trace_id for span in spans)
    attributes = json.dumps([dict(span.attributes) for span in spans])
    assert "private-user-jwt" not in attributes
    assert "private buyer wording" not in attributes
    provider.shutdown()


async def test_gateway_injects_current_w3c_trace_context():
    provider = TracerProvider()
    tracer = provider.get_tracer("test")
    client = StubClient()
    gateway = Gateway(Settings(agent_key="private-agent-key"), client_factory=lambda *a, **k: client)
    with tracer.start_as_current_span("request.pipeline") as span:
        async with gateway.session(Identity(str(USER_ID), "private-user-jwt")) as session:
            await session.call("get_facets", {}, dict)
        metadata = client.calls[0][2]["meta"]
        assert f"{span.get_span_context().trace_id:032x}" in metadata["traceparent"]
        assert "private-agent-key" not in json.dumps(metadata)
        assert "private-user-jwt" not in json.dumps(metadata)
    provider.shutdown()
