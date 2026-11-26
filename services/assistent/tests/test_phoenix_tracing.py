"""Unit tests for Arize Phoenix OpenTelemetry tracing integration."""

import os
from unittest.mock import ANY, MagicMock, patch
import pytest

from assistent.config import Settings
from assistent.observability.telemetry import (
    ExcludeHealthSpanProcessor,
    HealthFilterSampler,
    init_phoenix_tracing,
    is_phoenix_active,
)


def test_phoenix_config_defaults_and_env():
    settings = Settings(
        documents_url="http://mock-documents:8000",
        phoenix_enabled=True,
        phoenix_endpoint="http://custom-phoenix:6006/v1/traces",
        phoenix_project_name="custom-viio",
    )
    assert settings.phoenix_enabled is True
    assert settings.phoenix_endpoint == "http://custom-phoenix:6006/v1/traces"
    assert settings.phoenix_project_name == "custom-viio"


def test_init_phoenix_tracing_disabled_returns_false():
    result = init_phoenix_tracing(enabled=False)
    assert result is False


def test_init_phoenix_tracing_registers_provider():
    with patch("phoenix.otel.register") as mock_register, patch(
        "openinference.instrumentation.langchain.LangChainInstrumentor"
    ) as mock_instrumentor:
        mock_provider = MagicMock()
        mock_register.return_value = mock_provider
        mock_inst_instance = MagicMock()
        mock_instrumentor.return_value = mock_inst_instance

        # Reset global state for test
        import assistent.observability.telemetry as tm

        tm._PHOENIX_INITIALIZED = False

        success = init_phoenix_tracing(
            endpoint="http://127.0.0.1:6006/v1/traces",
            project_name="test-project",
            enabled=True,
            batch=True,
        )

        assert success is True
        assert is_phoenix_active() is True
        mock_register.assert_called_once_with(
            project_name="test-project",
            endpoint="http://127.0.0.1:6006/v1/traces",
            batch=True,
            verbose=False,
            sampler=ANY,
        )
        mock_inst_instance.instrument.assert_called_once_with(
            tracer_provider=mock_provider
        )


def test_init_phoenix_tracing_handles_exception_gracefully():
    with patch("phoenix.otel.register", side_effect=Exception("Connection refused")):
        import assistent.observability.telemetry as tm

        tm._PHOENIX_INITIALIZED = False

        success = init_phoenix_tracing(
            endpoint="http://invalid-endpoint:6006/v1/traces",
            project_name="fail-project",
            enabled=True,
        )

        assert success is False
        assert is_phoenix_active() is False


def test_health_filter_sampler_drops_health_and_metrics():
    sampler = HealthFilterSampler()

    # Drop by span name
    res_health = sampler.should_sample(None, 1, "GET /health")
    assert res_health.decision.name == "DROP"

    res_metrics = sampler.should_sample(None, 1, "GET /metrics")
    assert res_metrics.decision.name == "DROP"

    res_readyz = sampler.should_sample(None, 1, "GET /readyz")
    assert res_readyz.decision.name == "DROP"

    # Drop by route attribute
    res_route = sampler.should_sample(
        None, 1, "HTTP request", attributes={"http.route": "/health"}
    )
    assert res_route.decision.name == "DROP"

    # Drop by function name attribute (FastAPI internal spans)
    res_func = sampler.should_sample(
        None,
        1,
        "fastapi.endpoint",
        attributes={"code.function.name": "assistent.api.router.health"},
    )
    assert res_func.decision.name == "DROP"

    # Allow actual API and LLM spans
    res_chat = sampler.should_sample(
        None, 1, "POST /api/v1/chat", attributes={"http.route": "/api/v1/chat"}
    )
    assert res_chat.decision.name == "RECORD_AND_SAMPLE"

    res_langgraph = sampler.should_sample(None, 1, "LangGraph.node.coding_agent")
    assert res_langgraph.decision.name == "RECORD_AND_SAMPLE"


def test_exclude_health_span_processor_filters_spans():
    mock_wrapped = MagicMock()
    processor = ExcludeHealthSpanProcessor(mock_wrapped)

    # Health span should be dropped
    health_span = MagicMock()
    health_span.name = "GET /health"
    health_span.attributes = {"http.route": "/health"}
    processor.on_end(health_span)
    mock_wrapped.on_end.assert_not_called()

    # FastAPI internal health span should be dropped
    fastapi_span = MagicMock()
    fastapi_span.name = "fastapi.serialization"
    fastapi_span.attributes = {
        "code.function.name": "assistent.api.router.create_api_router.<locals>.health"
    }
    processor.on_end(fastapi_span)
    mock_wrapped.on_end.assert_not_called()

    # Legitimate chat/LLM span should be forwarded to wrapped processor
    chat_span = MagicMock()
    chat_span.name = "POST /api/v1/chat"
    chat_span.attributes = {"http.route": "/api/v1/chat"}
    processor.on_end(chat_span)
    mock_wrapped.on_end.assert_called_once_with(chat_span)
