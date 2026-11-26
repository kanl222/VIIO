"""Тесты для модуля конфигурации Settings с поддержкой LM Studio и Ollama."""

import pytest
from assistent.config import (
    DatabaseSettings,
    LLMSettings,
    MemgraphSettings,
    ObservabilitySettings,
    RAGClientSettings,
    ServerSettings,
    Settings,
    ToolsSettings,
)
from viio_documents.config import Settings as DocumentSettings


def test_public_and_internal_collections_must_differ():
    with pytest.raises(ValueError, match='должны различаться'):
        DocumentSettings(qdrant_public_collection_name='same', qdrant_collection_name='same')


def test_default_settings() -> None:
    """Проверка значений конфигурации по умолчанию с LM Studio в качестве основного провайдера."""
    settings = Settings(_env_file=None)

    assert settings.llm_provider == "lmstudio"
    assert settings.lmstudio_base_url == "http://localhost:1234/v1"
    assert settings.lmstudio_api_key == "lm-studio"
    assert settings.lmstudio_model == "qwen/qwen3-4b-2507"
    assert DocumentSettings(_env_file=None).lmstudio_embedding_model == "text-embedding-nomic-embed-text-v1.5"

    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.ollama_model == "llama3.1"
    assert DocumentSettings(_env_file=None).ollama_embedding_model == "nomic-embed-text"

    assert DocumentSettings(_env_file=None).qdrant_url == "http://localhost:6333"
    assert DocumentSettings(_env_file=None).qdrant_collection_name == "knowledge_base"
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
    assert DocumentSettings(_env_file=None).lmstudio_embedding_model == "text-embedding-multilingual-e5-base"
    assert settings.ollama_base_url == "http://custom-ollama:11434"
    assert settings.ollama_model == "mistral"
    assert DocumentSettings(_env_file=None).ollama_embedding_model == "bge-m3"
    assert DocumentSettings(_env_file=None).qdrant_url == "http://custom-qdrant:6333"
    assert DocumentSettings(_env_file=None).qdrant_collection_name == "custom_docs"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 9000


def test_modular_base_settings_classes() -> None:
    """Проверка независимой инициализации модульных BaseSettings классов."""
    llm = LLMSettings(_env_file=None)
    assert llm.llm_provider == "lmstudio"
    assert llm.coding_agent_enabled is True
    assert llm.lmstudio_base_url == "http://localhost:1234/v1"
    assert llm.parse_coding_allowed_roles("admin, dev") == ["admin", "dev"]
    assert llm.empty_routing_provider("") is None

    db = DatabaseSettings(_env_file=None)
    assert db.postgres_host == "localhost"
    assert db.postgres_port == 5432
    assert db.database_migrations_enabled is True

    mg = MemgraphSettings(_env_file=None)
    assert mg.memgraph_enabled is False
    assert mg.memgraph_uri == "bolt://localhost:7687"

    rag = RAGClientSettings(_env_file=None)
    assert rag.documents_url == "http://localhost:8002"
    assert rag.rag_url == "http://localhost:8001"
    assert rag.assistant_rag_backend == "local"

    tools = ToolsSettings(_env_file=None)
    assert tools.agent_max_tool_calls == 24
    assert tools.skills_dir == "skills"
    assert tools.skills_enabled is True
    assert tools.parse_github_allowed_repositories("org/repo1, org/repo2") == ["org/repo1", "org/repo2"]

    obs = ObservabilitySettings(_env_file=None)
    assert obs.phoenix_enabled is False
    assert obs.phoenix_project_name == "viio"

    server = ServerSettings(_env_file=None)
    assert server.api_host == "0.0.0.0"
    assert server.api_port == 8000
    assert server.runtime_keys() == ("chat_api_key", "documents_read_key")


def test_composite_settings_inheritance() -> None:
    """Проверка наследования и совместимости Settings со всеми модульными классами."""
    settings = Settings(_env_file=None)
    assert isinstance(settings, LLMSettings)
    assert isinstance(settings, DatabaseSettings)
    assert isinstance(settings, MemgraphSettings)
    assert isinstance(settings, RAGClientSettings)
    assert isinstance(settings, ToolsSettings)
    assert isinstance(settings, ObservabilitySettings)
    assert isinstance(settings, ServerSettings)
