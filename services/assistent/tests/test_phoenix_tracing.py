"""Unit tests for Arize Phoenix OpenTelemetry tracing integration."""

import os
from unittest.mock import MagicMock, patch
import pytest

from assistent.config import Settings
from assistent.telemetry import init_phoenix_tracing, is_phoenix_active


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
        import assistent.telemetry as tm

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
        )
        mock_inst_instance.instrument.assert_called_once_with(
            tracer_provider=mock_provider
        )


def test_init_phoenix_tracing_handles_exception_gracefully():
    with patch("phoenix.otel.register", side_effect=Exception("Connection refused")):
        import assistent.telemetry as tm

        tm._PHOENIX_INITIALIZED = False

        success = init_phoenix_tracing(
            endpoint="http://invalid-endpoint:6006/v1/traces",
            project_name="fail-project",
            enabled=True,
        )

        assert success is False
        assert is_phoenix_active() is False
