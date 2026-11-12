"""Optional OpenTelemetry setup and W3C trace-context propagation helpers."""
from __future__ import annotations

import os
import threading

from opentelemetry import trace
from opentelemetry.propagate import get_global_textmap
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

_LOCK = threading.Lock()
_PROVIDER_READY = False


def setup_tracing(service_name: str):
    """Configure OTLP export when enabled; otherwise use the OpenTelemetry no-op tracer."""
    global _PROVIDER_READY
    enabled = os.getenv("OTEL_ENABLED", "false").strip().casefold() in {"1", "true", "yes", "on"}
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if enabled and endpoint:
        with _LOCK:
            if not _PROVIDER_READY:
                provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
                provider.add_span_processor(BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=endpoint, timeout=float(os.getenv("OTEL_EXPORT_TIMEOUT_SECONDS", "3")))
                ))
                trace.set_tracer_provider(provider)
                _PROVIDER_READY = True
    return trace.get_tracer(service_name)


def inject_trace_context(carrier: dict | None = None) -> dict:
    """Inject the current W3C trace context into an HTTP or Celery header mapping."""
    carrier = carrier if carrier is not None else {}
    get_global_textmap().inject(carrier)
    return carrier


def extract_trace_context(carrier: dict | None = None):
    """Extract W3C trace context from an HTTP or Celery header mapping."""
    normalized = {str(key).lower(): str(value) for key, value in (carrier or {}).items() if value is not None}
    return get_global_textmap().extract(normalized)


def current_trace_ids() -> dict:
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return {}
    return {"trace_id": f"{span_context.trace_id:032x}", "span_id": f"{span_context.span_id:016x}"}
