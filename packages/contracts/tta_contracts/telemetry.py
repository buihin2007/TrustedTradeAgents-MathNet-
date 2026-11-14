"""Shared OpenTelemetry setup: build the OTLP settings for Langfuse from the LANGFUSE_* variables.

Call langfuse_otel_env() once at service start, before creating any tracer or exporter.
"""
import base64
import os


def langfuse_otel_env() -> None:
    pk, sk = os.environ.get("LANGFUSE_PUBLIC_KEY"), os.environ.get("LANGFUSE_SECRET_KEY")
    if not (pk and sk):
        return  # tracing disabled; spans become no-ops
    base = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com").rstrip("/")
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", f"{base}/api/public/otel")
    os.environ.setdefault("OTEL_EXPORTER_OTLP_HEADERS",
                          f"Authorization=Basic {auth},x-langfuse-ingestion-version=4")
    os.environ.setdefault("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
