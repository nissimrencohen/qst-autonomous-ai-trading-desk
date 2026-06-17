"""OpenTelemetry distributed tracing setup for the Agentic Engine.

Activated only when OTEL_EXPORTER_OTLP_ENDPOINT is set.

Beyond the base FastAPI + httpx auto-instrumentation shared by all services,
this module also registers a litellm CustomLogger that emits a child span for
every LLM completion, recording:
  - llm.request.model / llm.provider
  - llm.usage.input_tokens / output_tokens
  - llm.usage.cost_usd  (via litellm's built-in cost helper)
  - llm.latency_ms

Manual synthesis spans are added directly in engine.py via get_tracer().
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
_SERVICE_NAME_ENV = "OTEL_SERVICE_NAME"

_configured = False


def configure_otel(app, default_service_name: str) -> bool:
    """Instrument the agentic-engine FastAPI app with OTel tracing.

    Also wires the litellm OTel callback for per-completion child spans.
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

    # Register litellm callback so every LLM completion becomes a child span
    _register_litellm_callback()

    _configured = True
    log.info("OTel tracing active — service=%s endpoint=%s", svc, endpoint)
    return True


def _register_litellm_callback() -> None:
    """Attach the OTel span logger to litellm's global callback list."""
    try:
        import litellm
        cb = _LiteLLMOtelLogger()
        if cb not in litellm.callbacks:
            litellm.callbacks.append(cb)
        log.debug("litellm OTel callback registered")
    except ImportError:
        log.debug("litellm not available; skipping LLM span callback")


class _LiteLLMOtelLogger:
    """litellm CustomLogger that records each completion as an OTel span.

    Spans are created with explicit start/end timestamps so the recorded
    latency matches the actual provider round-trip, not the callback overhead.
    """

    def log_success_event(self, kwargs, response_obj, start_time, end_time) -> None:
        self._emit_span(kwargs, response_obj, start_time, end_time, error=None)

    def log_failure_event(self, kwargs, exception, start_time, end_time) -> None:
        self._emit_span(kwargs, None, start_time, end_time, error=exception)

    # litellm also calls async variants; delegate to sync
    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self.log_success_event(kwargs, response_obj, start_time, end_time)

    async def async_log_failure_event(self, kwargs, exception, start_time, end_time):
        self.log_failure_event(kwargs, exception, start_time, end_time)

    def _emit_span(self, kwargs, response_obj, start_time, end_time, error) -> None:
        try:
            from opentelemetry import trace
            from opentelemetry.trace import SpanKind, StatusCode
        except ImportError:
            return

        tracer = trace.get_tracer("litellm")
        model = kwargs.get("model", "unknown")
        provider = kwargs.get("custom_llm_provider", "")

        # Convert datetime → nanoseconds for the OTel span timestamps
        start_ns = int(start_time.timestamp() * 1_000_000_000)
        end_ns = int(end_time.timestamp() * 1_000_000_000)

        span = tracer.start_span(
            "llm.completion",
            start_time=start_ns,
            kind=SpanKind.CLIENT,
        )
        try:
            span.set_attribute("llm.system", "litellm")
            span.set_attribute("llm.request.model", model)
            span.set_attribute("llm.provider", provider)
            span.set_attribute(
                "llm.latency_ms",
                round((end_time - start_time).total_seconds() * 1000, 1),
            )
            if response_obj is not None:
                usage = getattr(response_obj, "usage", None)
                if usage:
                    span.set_attribute(
                        "llm.usage.input_tokens",
                        getattr(usage, "prompt_tokens", 0) or 0,
                    )
                    span.set_attribute(
                        "llm.usage.output_tokens",
                        getattr(usage, "completion_tokens", 0) or 0,
                    )
                try:
                    import litellm
                    cost = litellm.completion_cost(completion_response=response_obj)
                    if cost:
                        span.set_attribute("llm.usage.cost_usd", round(cost, 8))
                except Exception:
                    pass

            if error is not None:
                span.set_status(StatusCode.ERROR, str(error))
            else:
                span.set_status(StatusCode.OK)
        finally:
            span.end(end_time=end_ns)


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
