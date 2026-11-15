"""Per-app tracing, with opt-in Langfuse export and no token/prompt attributes."""

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
