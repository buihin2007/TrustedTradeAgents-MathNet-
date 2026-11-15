"""Per-app tracing, with opt-in Langfuse export and no token/prompt attributes."""

import json
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from tta_contracts.telemetry import langfuse_otel_env


def create_provider():
    langfuse_otel_env()  # OTEL_EXPORTER_OTLP_* from LANGFUSE_PUBLIC_KEY / SECRET_KEY / BASE_URL
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", "tta-api"),
            }
        )
    )
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") and os.getenv("OTEL_EXPORTER_OTLP_HEADERS"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(timeout=5)))
    return provider


def current_trace_id():
    context = trace.get_current_span().get_span_context()
    return f"{context.trace_id:032x}" if context.is_valid else None


def capture() -> bool:
    """Send inputs/outputs (buyer text, prompts, tool arguments) to Langfuse. TRACE_CONTENT=0 turns it off.
    Tokens and keys are never attached."""
    return os.getenv("TRACE_CONTENT", "1") != "0"


def io(span, input=None, output=None, **attributes) -> None:
    """Set Langfuse input/output (JSON, at most 20 kB each) and plain attributes on a span."""
    for key, value in attributes.items():
        if value is not None:
            span.set_attribute(key, value)
    if not capture():
        return
    for key, value in (("langfuse.observation.input", input), ("langfuse.observation.output", output)):
        if value is not None:
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
            span.set_attribute(key, text[:20000])
