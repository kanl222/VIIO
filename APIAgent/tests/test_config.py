"""Тесты для модуля конфигурации Settings с поддержкой LM Studio и Ollama."""

import pytest
from apiagent.config import Settings


def test_default_settings() -> None:
    """Проверка значений конфигурации по умолчанию с LM Studio в качестве основного провайдера."""
    settings = Settings(_env_file=None)

    assert settings.llm_provider == "lmstudio"
    assert settings.lmstudio_base_url == "http://localhost:1234/v1"
    assert settings.lmstudio_api_key == "lm-studio"
    assert settings.lmstudio_model == "qwen/qwen3-4b-2507"
    assert settings.lmstudio_embedding_model == "text-embedding-nomic-embed-text-v1.5"

    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.ollama_model == "llama3.1"
    assert settings.ollama_embedding_model == "nomic-embed-text"

    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.qdrant_collection_name == "knowledge_base"
    assert settings.api_host == "0.0.0.0"
    assert settings.api_port == 8000


def test_settings_override_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверка переопределения конфигурации через переменные окружения."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://custom-lms:1234/v1")
    monkeypatch.setenv("LMSTUDIO_MODEL", "qwen/qwen3.5-9b")
    monkeypatch.setenv("LMSTUDIO_EMBEDDING_MODEL", "text-embedding-multilingual-e5-base")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://custom-ollama:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")
    monkeypatch.setenv("OLLAMA_EMBEDDING_MODEL", "bge-m3")
    monkeypatch.setenv("QDRANT_URL", "http://custom-qdrant:6333")
    monkeypatch.setenv("QDRANT_COLLECTION_NAME", "custom_docs")
    monkeypatch.setenv("API_HOST", "127.0.0.1")
    monkeypatch.setenv("API_PORT", "9000")

    settings = Settings()

    assert settings.llm_provider == "ollama"
    assert settings.lmstudio_base_url == "http://custom-lms:1234/v1"
    assert settings.lmstudio_model == "qwen/qwen3.5-9b"
    assert settings.lmstudio_embedding_model == "text-embedding-multilingual-e5-base"
    assert settings.ollama_base_url == "http://custom-ollama:11434"
    assert settings.ollama_model == "mistral"
    assert settings.ollama_embedding_model == "bge-m3"
    assert settings.qdrant_url == "http://custom-qdrant:6333"
    assert settings.qdrant_collection_name == "custom_docs"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 9000
