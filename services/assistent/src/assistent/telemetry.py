"""Arize Phoenix OpenTelemetry tracing integration for LangChain and LangGraph."""

import logging
import os

logger = logging.getLogger(__name__)

_PHOENIX_INITIALIZED = False


def init_phoenix_tracing(
    endpoint: str | None = None,
    project_name: str | None = None,
    enabled: bool = True,
    batch: bool = True,
) -> bool:
    """Initialize Arize Phoenix OpenTelemetry instrumentation for LangChain/LangGraph.

    Sends spans for LLM calls, chain runs, tool calls, and LangGraph node executions
    to a local or remote Arize Phoenix collector via OTLP (OpenTelemetry Protocol).

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

    ep = endpoint or os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006/v1/traces")
    proj = project_name or os.getenv("PHOENIX_PROJECT_NAME", "viio")

    try:
        from phoenix.otel import register
        from openinference.instrumentation.langchain import LangChainInstrumentor

        tracer_provider = register(
            project_name=proj,
            endpoint=ep,
            batch=batch,
            verbose=False,
        )
        LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
        _PHOENIX_INITIALIZED = True
        logger.info(
            "Arize Phoenix OpenTelemetry instrumentation active (project: %s, endpoint: %s)",
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
