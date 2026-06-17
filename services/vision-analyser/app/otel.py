"""OpenTelemetry distributed tracing setup for the Vision Analyser.

Activated only when OTEL_EXPORTER_OTLP_ENDPOINT is set.
See rag-service/app/otel.py for full documentation.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
_SERVICE_NAME_ENV = "OTEL_SERVICE_NAME"

_configured = False


def configure_otel(app, default_service_name: str) -> bool:
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
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        log.warning("OTel packages not available (%s); tracing disabled", exc)
        return False

    svc = os.getenv(_SERVICE_NAME_ENV, default_service_name)
    resource = Resource({SERVICE_NAME: svc})
    provider = TracerProvider(resource=resource)
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
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except ImportError:
        return _NoOpTracer()


class _NoOpTracer:
    def start_as_current_span(self, *args, **kwargs):
        from contextlib import contextmanager

        @contextmanager
        def _noop():
            yield None

        return _noop()
