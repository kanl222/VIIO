"""Arize Phoenix OpenTelemetry tracing integration for LangChain and LangGraph."""

import contextvars
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_PHOENIX_INITIALIZED = False
_EXCLUDED_PATTERNS = ("health", "readyz", "metrics", "healthz", "ping")
_current_root_span: contextvars.ContextVar[Any] = contextvars.ContextVar("current_root_span", default=None)


class HealthFilterSampler:
    """OpenTelemetry Sampler that drops health check, readiness, and metrics traces."""

    def __init__(self, fallback_sampler: Any = None) -> None:
        self._fallback = fallback_sampler

    def should_sample(
        self,
        parent_context: Any,
        trace_id: int,
        name: str,
        kind: Any = None,
        attributes: dict[str, Any] | None = None,
        links: Any = None,
    ) -> Any:
        from opentelemetry.sdk.trace.sampling import Decision, SamplingResult

        name_lower = (name or "").lower()
        if any(pat in name_lower for pat in _EXCLUDED_PATTERNS):
            return SamplingResult(Decision.DROP)
        if attributes:
            route = str(
                attributes.get("http.route")
                or attributes.get("url.path")
                or attributes.get("http.target")
                or ""
            ).lower()
            if any(pat in route for pat in _EXCLUDED_PATTERNS):
                return SamplingResult(Decision.DROP)
            func = str(attributes.get("code.function.name") or "").lower()
            if any(pat in func for pat in _EXCLUDED_PATTERNS):
                return SamplingResult(Decision.DROP)
        if self._fallback:
            return self._fallback.should_sample(parent_context, trace_id, name, kind, attributes, links)
        return SamplingResult(Decision.RECORD_AND_SAMPLE)

    def get_description(self) -> str:
        return "HealthFilterSampler"


class ExcludeHealthSpanProcessor:
    """SpanProcessor wrapper that drops health check spans at end time."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)

    def _on_ending(self, span: Any) -> None:
        if hasattr(self._wrapped, "_on_ending"):
            self._wrapped._on_ending(span)

    def on_start(self, span: Any, parent_context: Any = None) -> None:
        if getattr(span, "parent", None) is None:
            _current_root_span.set(span)
        self._wrapped.on_start(span, parent_context)

    def on_end(self, span: Any) -> None:
        name = (getattr(span, "name", "") or "").lower()
        if any(pat in name for pat in _EXCLUDED_PATTERNS):
            return
        attrs = getattr(span, "attributes", None) or {}
        route = str(
            attrs.get("http.route")
            or attrs.get("url.path")
            or attrs.get("http.target")
            or ""
        ).lower()
        if any(pat in route for pat in _EXCLUDED_PATTERNS):
            return
        func = str(attrs.get("code.function.name") or "").lower()
        if any(pat in func for pat in _EXCLUDED_PATTERNS):
            return

        # Auto-propagate input/output/session to root span if root span is missing them
        if name in ("langgraph", "agent", "chatopenai"):
            root = _current_root_span.get()
            if root and getattr(root, "is_recording", lambda: False)():
                root_attrs = getattr(root, "attributes", None) or {}
                if "input.value" not in root_attrs and "input.value" in attrs:
                    root.set_attribute("input.value", attrs["input.value"])
                if "output.value" not in root_attrs and "output.value" in attrs:
                    root.set_attribute("output.value", attrs["output.value"])
                if "session.id" not in root_attrs and "session.id" in attrs:
                    root.set_attribute("session.id", attrs["session.id"])

        self._wrapped.on_end(span)

    def shutdown(self) -> None:
        self._wrapped.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._wrapped.force_flush(timeout_millis)


def record_telemetry_turn_start(
    message: str,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Record turn user input and session context on root span and active span for Phoenix."""
    from opentelemetry import trace

    targets = []
    root = _current_root_span.get()
    if root and getattr(root, "is_recording", lambda: False)():
        targets.append(root)
    curr = trace.get_current_span()
    if curr and getattr(curr, "is_recording", lambda: False)() and curr is not root:
        targets.append(curr)

    for sp in targets:
        sp.set_attribute("openinference.span.kind", "CHAIN")
        sp.set_attribute("input.value", message)
        sp.set_attribute("input.mime_type", "text/plain")
        if thread_id:
            sp.set_attribute("session.id", str(thread_id))
        if user_id:
            sp.set_attribute("user.id", str(user_id))


def record_telemetry_turn_end(
    reply: str,
    thread_id: str | None = None,
) -> None:
    """Record turn assistant output on root span and active span for Phoenix."""
    from opentelemetry import trace

    targets = []
    root = _current_root_span.get()
    if root and getattr(root, "is_recording", lambda: False)():
        targets.append(root)
    curr = trace.get_current_span()
    if curr and getattr(curr, "is_recording", lambda: False)() and curr is not root:
        targets.append(curr)

    for sp in targets:
        sp.set_attribute("output.value", reply)
        sp.set_attribute("output.mime_type", "text/plain")
        if thread_id:
            sp.set_attribute("session.id", str(thread_id))


def init_phoenix_tracing(
    endpoint: str | None = None,
    project_name: str | None = None,
    enabled: bool = True,
    batch: bool = True,
) -> bool:
    """Initialize Arize Phoenix OpenTelemetry instrumentation for LangChain/LangGraph.

    Sends spans for LLM calls, chain runs, tool calls, and LangGraph node executions
    to a local or remote Arize Phoenix collector via OTLP (OpenTelemetry Protocol).
    Health check endpoints (/health, /metrics, etc.) are filtered out.

    Args:
        endpoint: Phoenix OTLP collector URL (default: http://localhost:6006/v1/traces
                  or PHOENIX_COLLECTOR_ENDPOINT env var).
        project_name: Project name in Phoenix (default: viio or PHOENIX_PROJECT_NAME env var).
        enabled: Whether Phoenix tracing is enabled.
        batch: Whether to use BatchSpanProcessor for async non-blocking span export.

    Returns:
        bool: True if instrumentation was initialized, False otherwise.
    """
    global _PHOENIX_INITIALIZED
    if not enabled:
        return False
    if _PHOENIX_INITIALIZED:
        return True

    ep = (
        endpoint
        or os.getenv("PHOENIX_COLLECTOR_ENDPOINT")
        or os.getenv("PHOENIX_ENDPOINT")
        or "http://localhost:6006/v1/traces"
    )
    proj = project_name or os.getenv("PHOENIX_PROJECT_NAME", "viio")

    try:
        from phoenix.otel import register
        from openinference.instrumentation.langchain import LangChainInstrumentor

        tracer_provider = register(
            project_name=proj,
            endpoint=ep,
            batch=batch,
            verbose=False,
            sampler=HealthFilterSampler(),
        )

        raw_processor = getattr(tracer_provider, "_default_processor", None)
        if raw_processor:
            wrapped = ExcludeHealthSpanProcessor(raw_processor)
            tracer_provider._default_processor = wrapped
            if hasattr(tracer_provider, "_active_span_processor") and hasattr(
                tracer_provider._active_span_processor, "_span_processors"
            ):
                procs = list(tracer_provider._active_span_processor._span_processors)
                for i, p in enumerate(procs):
                    if p is raw_processor:
                        procs[i] = wrapped
                tracer_provider._active_span_processor._span_processors = tuple(procs)

        LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
        _PHOENIX_INITIALIZED = True
        logger.info(
            "Arize Phoenix OpenTelemetry instrumentation active (project: %s, endpoint: %s, health filter enabled)",
            proj,
            ep,
        )
        return True
    except Exception as exc:
        logger.warning("Failed to initialize Arize Phoenix tracing: %s", exc)
        return False


def is_phoenix_active() -> bool:
    """Return whether Phoenix tracing has been initialized in this process."""
    return _PHOENIX_INITIALIZED
