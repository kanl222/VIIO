"""Модуль конфигурации микросервиса assistent.

Содержит модульные классы настроек приложения на основе Pydantic Settings,
считываемые из переменных окружения и .env файлов:
- LLMSettings: Провайдеры, модели chat/coding/routing/database, параметры генерации.
- DatabaseSettings: Подключение к PostgreSQL и управление миграциями.
- MemgraphSettings: Параметры графа знаний Memgraph (Semantic GraphRAG).
- RAGClientSettings: URL сервисов documents/rag, тайм-ауты, лимиты контекста и консультант.
- ToolsSettings: Настройки инструментов MCP, PostgreSQL tool, GitHub, Skills и лимиты вызовов.
- ObservabilitySettings: Настройки телеметрии Phoenix, суммаризации и истории диалогов.
- ServerSettings: Сетевые настройки FastAPI и ключи безопасности/авторизации.
- Settings: Композитный класс, объединяющий все настройки с сохранением 100% обратной совместимости.
"""

import json
from typing import Any, Literal
from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """Настройки LLM-провайдеров, моделей и параметров генерации."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: Literal["lmstudio", "ollama", "yandex"] = Field(
        default="lmstudio",
        description="Общий провайдер агентов: 'lmstudio', 'ollama' или 'yandex'.",
    )

    # Настройки LM Studio (OpenAI-compatible)
    lmstudio_base_url: str = Field(
        default="http://localhost:1234/v1",
        description="Базовый URL к локальному серверу LM Studio.",
    )
    lmstudio_api_key: str = Field(
        default="lm-studio",
        repr=False,
        description="Ключ авторизации для запросов к LM Studio.",
    )
    lmstudio_model: str = Field(
        default="qwen/qwen3-4b-2507",
        description="Идентификатор модели LM Studio для рассуждений агента.",
    )

    # Настройки Ollama (обратная совместимость)
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Базовый URL для подключения к Ollama.",
    )
    ollama_model: str = Field(
        default="llama3.1",
        description="Идентификатор модели Ollama для рассуждений агента.",
    )

    # Настройки Yandex Cloud
    yandex_api_key: str = Field(default="", repr=False)
    yandex_folder_id: str = ""
    yandex_model: str = "aliceai-llm"
    yandex_timeout_seconds: int = Field(default=60, ge=5, le=120)

    # Специализированные роли моделей
    chat_provider: Literal["lmstudio", "ollama", "yandex"] | None = None
    chat_model: str | None = None
    chat_timeout_seconds: int = Field(default=120, ge=5, le=600)
    chat_temperature: float = Field(default=0.0, ge=0, le=2)
    chat_top_p: float = Field(default=1.0, gt=0, le=1)
    chat_max_tokens: int = Field(default=2048, ge=128, le=32768)

    routing_model: str | None = None
    routing_provider: Literal["lmstudio", "ollama", "yandex"] | None = None
    routing_max_tokens: int = Field(default=128, ge=16, le=1024)

    coding_agent_enabled: bool = True
    coding_provider: Literal["lmstudio", "ollama", "yandex"] | None = None
    coding_model: str | None = None
    coding_temperature: float = Field(default=0.0, ge=0, le=2)
    coding_top_p: float = Field(default=1.0, gt=0, le=1)
    coding_max_tokens: int = Field(default=4096, ge=128, le=32768)
    coding_allowed_roles: list[str] | str = Field(default_factory=lambda: ["programmer", "admin"])
    coding_context_max_chars: int = Field(default=6000, ge=1000, le=24000)

    database_provider: Literal["lmstudio", "ollama", "yandex"] | None = None
    database_model: str | None = None

    supervisor_enabled: bool = False
    model_first_tools: bool = True

    @field_validator("coding_allowed_roles", mode="before")
    @classmethod
    def parse_coding_allowed_roles(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return ["programmer", "admin"]
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return ["programmer", "admin"]
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                if not inner:
                    return []
                try:
                    return json.loads(value)
                except Exception:
                    return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
            return [item.strip().strip("'\"") for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return value

    @field_validator("routing_provider", mode="before")
    @classmethod
    def empty_routing_provider(cls, value: Any) -> Any:
        return value or None


class DatabaseSettings(BaseSettings):
    """Настройки PostgreSQL для хранения состояния агента (LangGraph Checkpointer) и миграций."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_host: str = Field(
        default="localhost",
        description="Хост для подключения к PostgreSQL.",
    )
    postgres_port: int = Field(
        default=5432,
        ge=1,
        le=65535,
        description="Порт для подключения к PostgreSQL.",
    )
    postgres_db: str = Field(
        default="agent_db",
        description="Имя базы данных агента.",
    )
    postgres_user: str = Field(
        default="postgres",
        description="Пользователь базы данных PostgreSQL.",
    )
    postgres_password: str = Field(
        default="postgres",
        repr=False,
        description="Пароль пользователя PostgreSQL.",
    )
    database_migrations_enabled: bool = True


class MemgraphSettings(BaseSettings):
    """Настройки графа знаний Memgraph (Semantic GraphRAG)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    memgraph_enabled: bool = Field(default=False, description="Включить поиск по графу знаний Memgraph.")
    memgraph_uri: str = Field(default="bolt://localhost:7687", description="Bolt URI подключения к Memgraph.")
    memgraph_user: str = Field(default="", description="Пользователь Memgraph.")
    memgraph_password: str = Field(default="", repr=False, description="Пароль Memgraph.")
    memgraph_timeout_seconds: int = Field(default=5, ge=1, le=60, description="Таймаут запросов к Memgraph в секундах.")
    memgraph_max_stems: int = Field(default=5, ge=1, le=20, description="Максимальное количество основ запроса для графа.")


class RAGClientSettings(BaseSettings):
    """Настройки клиентов RAG, внешних микросервисов и консультанта."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    documents_url: str = "http://localhost:8002"
    rag_url: str = "http://localhost:8001"
    assistant_rag_backend: Literal["local", "yandex"] = "local"
    rag_timeout_seconds: int = Field(default=120, ge=5, le=600)
    rag_context_max_chars: int = Field(default=4500, ge=1000, le=48000)
    corrective_rag_enabled: bool = False

    consultant_enabled: bool = False
    consultant_api_key: str | None = Field(default=None, repr=False)
    consultant_source_key: str | None = Field(default=None, repr=False)
    consultant_url: str = "http://consultant:8000"


class ToolsSettings(BaseSettings):
    """Настройки инструментов агента (MCP, PostgreSQL tool, GitHub, Skills, лимиты вызовов)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    agent_max_tool_calls: int = Field(default=24, ge=3, le=64)
    hitl_enabled: bool = False
    text_to_sql_enabled: bool = True
    skills_enabled: bool = False
    skills_dir: str = "skills"

    github_mcp_enabled: bool = False
    github_mcp_token: str = Field(default="", repr=False)
    github_repository: str = ""
    github_allowed_repositories: list[str] = Field(default_factory=list)
    github_mcp_max_chars: int = Field(default=16000, ge=1000, le=48000)
    github_mcp_timeout_seconds: int = Field(default=25, ge=5, le=60)

    pgsql_enabled: bool = False
    pgsql_mcp_url: str = "http://postgres-tool:8000/sse"
    pgsql_max_rows: int = Field(default=100, ge=1, le=500)
    pgsql_max_chars: int = Field(default=12000, ge=500, le=50000)
    pgsql_timeout_seconds: int = Field(default=40, ge=5, le=90)

    @field_validator("github_allowed_repositories", mode="before")
    @classmethod
    def parse_github_allowed_repositories(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return []
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                if not inner:
                    return []
                try:
                    return json.loads(value)
                except Exception:
                    return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
            return [item.strip().strip("'\"") for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return value


class ObservabilitySettings(BaseSettings):
    """Настройки мониторинга, трассировки Phoenix и суммаризации истории диалогов."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    phoenix_enabled: bool = False
    phoenix_endpoint: str = Field(
        default="http://localhost:6006/v1/traces",
        validation_alias=AliasChoices("PHOENIX_ENDPOINT", "PHOENIX_COLLECTOR_ENDPOINT", "phoenix_endpoint"),
    )
    phoenix_project_name: str = Field(
        default="viio",
        validation_alias=AliasChoices("PHOENIX_PROJECT_NAME", "phoenix_project_name"),
    )
    conversation_summary_enabled: bool = True
    conversation_summary_after: int = Field(default=24, ge=12, le=100)
    conversation_history_max_chars: int = Field(default=18000, ge=4000, le=60000)


class ServerSettings(BaseSettings):
    """Настройки HTTP-сервера FastAPI, роли сервиса и токенов безопасности."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_role: Literal["assistent"] = "assistent"
    api_host: str = Field(
        default="0.0.0.0",
        description="Сетевой интерфейс для привязки FastAPI сервера.",
    )
    api_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="Порт для привязки FastAPI сервера.",
    )
    internal_api_key: str | None = Field(
        default=None,
        repr=False,
        description="Секретный токен для межсервисной аутентификации (X-Internal-Token).",
    )
    secure_runtime: bool = False
    chat_api_key: str | None = Field(default=None, repr=False)
    chat_api_key_previous: str | None = Field(default=None, repr=False)
    documents_read_key: str | None = Field(default=None, repr=False)
    documents_admin_key: str | None = Field(default=None, repr=False)

    def runtime_keys(self) -> tuple[str, str]:
        """Возвращает список ключей, обязательных в secure_runtime."""
        return ("chat_api_key", "documents_read_key")

    def validate_runtime(self) -> None:
        """Проверяет безопасность токенов для режима secure_runtime."""
        if not self.secure_runtime:
            return
        required = self.runtime_keys()
        values = [getattr(self, key) for key in required]
        if any(not value or len(value) < 32 or "viio-internal-secret-token" in value for value in values):
            raise ValueError("Production requires distinct service keys of at least 32 characters")
        if len(set(values)) != len(values):
            raise ValueError("Service keys must have distinct values")
        if self.documents_admin_key and (len(self.documents_admin_key) < 32 or self.documents_admin_key in values):
            raise ValueError("Document write key must be long and distinct")
        if self.chat_api_key_previous and (len(self.chat_api_key_previous) < 32 or self.chat_api_key_previous in values):
            raise ValueError("Previous chat key must be long and distinct")


class Settings(
    LLMSettings,
    DatabaseSettings,
    MemgraphSettings,
    RAGClientSettings,
    ToolsSettings,
    ObservabilitySettings,
    ServerSettings,
):
    """Единый композитный класс настроек сервиса assistent.

    Объединяет настройки LLM, базы данных, графа, RAG, инструментов,
    наблюдаемости и сервера, обеспечивая полную обратную совместимость.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_cloud_models(self) -> "Settings":
        """Проверяет валидность настроек облачных моделей (например, Yandex)."""
        roles = ("chat", "coding", "database")
        if (self.routing_model or "").strip():
            roles += ("routing",)
        providers = {
            role: getattr(self, f"{role}_provider") or self.llm_provider
            for role in roles
        }
        if "yandex" in providers.values():
            if not self.yandex_api_key.strip() or not self.yandex_folder_id.strip():
                raise ValueError("Yandex requires YANDEX_API_KEY and YANDEX_FOLDER_ID")
            for role in roles:
                if providers[role] == "yandex":
                    name = getattr(self, f"{role}_model") or self.yandex_model
                    if not name.strip():
                        raise ValueError("Yandex model must not be empty")
        return self


__all__ = [
    "LLMSettings",
    "DatabaseSettings",
    "MemgraphSettings",
    "RAGClientSettings",
    "ToolsSettings",
    "ObservabilitySettings",
    "ServerSettings",
    "Settings",
]
