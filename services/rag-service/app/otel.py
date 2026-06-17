"""OpenTelemetry distributed tracing setup for the RAG service.

Activated only when OTEL_EXPORTER_OTLP_ENDPOINT is set in the environment.
Standard OTel env vars are honoured automatically:
  OTEL_EXPORTER_OTLP_ENDPOINT  — collector base URL (e.g. http://otel-collector:4318)
  OTEL_SERVICE_NAME            — overrides the default service name
  OTEL_EXPORTER_OTLP_HEADERS  — comma-separated key=value auth headers

When the endpoint is absent all calls are no-ops; no packages need to be
imported at runtime in that path.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
_SERVICE_NAME_ENV = "OTEL_SERVICE_NAME"

_configured = False


def configure_otel(app, default_service_name: str) -> bool:
    """Instrument a FastAPI app with OTel tracing.

    Returns True if tracing was successfully activated.
    """
    global _configured
    endpoint = os.getenv(_ENDPOINT_ENV, "").strip()
    if not endpoint:
        log.debug("OTel disabled — set %s to enable tracing", _ENDPOINT_ENV)
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.resources import SERVICE_NAME
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        log.warning("OTel packages not available (%s); tracing disabled", exc)
        return False

    svc = os.getenv(_SERVICE_NAME_ENV, default_service_name)
    resource = Resource({SERVICE_NAME: svc})
    provider = TracerProvider(resource=resource)

    # Append the standard /v1/traces path expected by OTLP HTTP collectors
    traces_url = endpoint.rstrip("/") + "/v1/traces"
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_url))
    )
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()

    _configured = True
    log.info("OTel tracing active — service=%s endpoint=%s", svc, endpoint)
    return True


def get_tracer(name: str):
    """Return a tracer from the active provider.

    Safe to call even when OTel is not configured — returns a no-op tracer.
    """
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except ImportError:
        return _NoOpTracer()


class _NoOpTracer:
    """Fallback when opentelemetry-sdk is not installed."""

    def start_as_current_span(self, *args, **kwargs):
        from contextlib import contextmanager

        @contextmanager
        def _noop():
            yield None

        return _noop()
