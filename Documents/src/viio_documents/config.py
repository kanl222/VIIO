"""Модуль конфигурации микросервиса viio_documents.

Содержит настройки приложения, считываемые из переменных окружения и .env файлов
на основе Pydantic Settings. Поддерживает работу с LM Studio и Ollama.
"""

from typing import Literal
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки конфигурации сервиса viio_documents.

    Attributes:
        llm_provider: Провайдер LLM ('lmstudio' или 'ollama').
        lmstudio_base_url: Базовый URL для подключения к LM Studio (OpenAI-совместимый API).
        lmstudio_api_key: Ключ авторизации для LM Studio.
        lmstudio_model: Имя модели LLM в LM Studio для генерации ответов.
        lmstudio_embedding_model: Имя модели в LM Studio для формирования эмбеддингов.
        ollama_base_url: Базовый URL для подключения к сервису Ollama.
        ollama_model: Имя модели LLM для генерации ответов в Ollama.
        ollama_embedding_model: Имя модели для формирования эмбеддингов в Ollama.
        qdrant_url: URL векторного хранилища Qdrant.
        qdrant_collection_name: Имя коллекции в Qdrant для хранения документов.
        postgres_host: Хост для подключения к PostgreSQL.
        postgres_port: Порт для подключения к PostgreSQL.
        postgres_db: Имя базы данных агента.
        postgres_user: Пользователь базы данных PostgreSQL.
        postgres_password: Пароль пользователя PostgreSQL.
        internal_api_key: Секретный токен для межсервисной аутентификации (X-Internal-Token).
        api_host: Хост для запуска HTTP сервера FastAPI.
        api_port: Порт для запуска HTTP сервера FastAPI.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: Literal["lmstudio", "ollama"] = Field(
        default="lmstudio",
        description="Активный провайдер языковых моделей: 'lmstudio' или 'ollama'.",
    )

    coding_agent_enabled: bool = True
    coding_model: str | None = None
    coding_allowed_roles: list[str] = Field(default_factory=lambda: ["programmer", "admin"])
    coding_context_max_chars: int = Field(default=6000, ge=1000, le=24000)
    conversation_summary_enabled: bool = True
    conversation_summary_after: int = Field(default=24, ge=12, le=100)
    conversation_history_max_chars: int = Field(default=18000, ge=4000, le=60000)
    consultant_enabled: bool = False
    consultant_api_key: str | None = None
    consultant_source_key: str | None = None
    consultant_url: str = 'http://consultant:8000'
    # Independent generation budgets for the consultant and text-only coder.
    chat_temperature: float = Field(default=0.0, ge=0, le=2)
    chat_top_p: float = Field(default=1.0, gt=0, le=1)
    chat_max_tokens: int = Field(default=2048, ge=128, le=32768)
    coding_temperature: float = Field(default=0.0, ge=0, le=2)
    coding_top_p: float = Field(default=1.0, gt=0, le=1)
    coding_max_tokens: int = Field(default=4096, ge=128, le=32768)

    pgsql_enabled: bool = False
    pgsql_mcp_url: str = "http://postgres-tool:8000/sse"
    pgsql_max_rows: int = Field(default=100, ge=1, le=500)
    pgsql_max_chars: int = Field(default=12000, ge=500, le=50000)
    pgsql_timeout_seconds: int = Field(default=40, ge=5, le=90)
    # Настройки каталога документов и файлового хранилища
    documents_storage_path: str = Field(
        default="data/documents",
        description="Путь к директории хранилища оригиналов документов (Docker-том).",
    )
    documents_catalog_enabled: bool = Field(
        default=True,
        description="Вести каталог документов в PostgreSQL (рекомендуется).",
    )
    # Массовая загрузка
    bulk_max_files: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Максимальное число файлов в одной пакетной загрузке.",
    )
    bulk_max_file_bytes: int = Field(
        default=10 * 1024 * 1024,
        ge=1024,
        le=50 * 1024 * 1024,
        description="Максимальный размер одного файла при массовой загрузке (байт).",
    )
    bulk_max_workers: int = Field(
        default=1,
        ge=1,
        le=8,
        description="Максимальное число параллельных фоновых воркеров очереди.",
    )

    pdf_engine: Literal["auto", "pypdf", "docling"] = "auto"
    pdf_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024)
    pdf_max_pages: int = Field(default=50, ge=1, le=200)
    pdf_max_chars: int = Field(default=500000, ge=100, le=500000)
    pdf_timeout_seconds: int = Field(default=120, ge=10, le=300)
    docling_url: str = "http://pdf-worker:8080"

    # Настройки LM Studio (OpenAI-compatible)
    lmstudio_base_url: str = Field(
        default="http://localhost:1234/v1",
        description="Базовый URL к локальному серверу LM Studio.",
    )
    lmstudio_api_key: str = Field(
        default="lm-studio",
        description="Ключ авторизации для запросов к LM Studio.",
    )
    lmstudio_model: str = Field(
        default="qwen/qwen3-4b-2507",
        description="Идентификатор модели LM Studio для рассуждений агента.",
    )
    lmstudio_embedding_model: str = Field(
        default="text-embedding-nomic-embed-text-v1.5",
        description="Идентификатор модели LM Studio для создания эмбеддингов.",
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
    ollama_embedding_model: str = Field(
        default="nomic-embed-text",
        description="Идентификатор модели Ollama для генерации эмбеддингов.",
    )

    # Настройки векторного хранилища и API
    qdrant_url: str = Field(
        default="http://localhost:6333",
        description="URL для подключения к Qdrant.",
    )
    qdrant_collection_name: str = Field(
        default="knowledge_base",
        description="Название коллекции векторов в Qdrant.",
    )
    qdrant_public_collection_name: str = Field(default='knowledge_public', min_length=1)

    @model_validator(mode='after')
    def validate_public_collection(self):
        if self.qdrant_public_collection_name == self.qdrant_collection_name:
            raise ValueError('Публичная и внутренняя коллекции Qdrant должны различаться')
        return self

    # Настройки PostgreSQL для хранения состояния агента (LangGraph Checkpointer)
    rag_chunk_unit: Literal["characters", "words"] = "characters"
    rag_chunk_size: int = Field(default=500, gt=0)
    rag_chunk_overlap: int = Field(default=50, ge=0)
    rag_context_max_chars: int = Field(default=12000, ge=1000, le=48000)

    @model_validator(mode="after")
    def validate_chunk_overlap(self) -> "Settings":
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP должен быть меньше RAG_CHUNK_SIZE")
        return self

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
        description="Пароль пользователя PostgreSQL.",
    )
    internal_api_key: str | None = Field(
        default=None,
        description="Секретный токен для межсервисной аутентификации (X-Internal-Token).",
    )

    service_role: Literal['documents', 'document-worker'] = 'documents'
    documents_url: str = 'http://localhost:8002'
    document_worker_url: str = 'http://document-worker:8000'

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

    secure_runtime: bool = False
    database_migrations_enabled: bool = True
    chat_api_key: str | None = None
    documents_read_key: str | None = None
    documents_read_key_previous: str | None = None
    documents_admin_key: str | None = None
    documents_admin_key_previous: str | None = None
    document_worker_key: str | None = None
    document_worker_key_previous: str | None = None
    qdrant_api_key: str | None = None
    docling_api_key: str | None = None

    def validate_runtime(self):
        if not self.secure_runtime:
            return
        required = self.runtime_keys()
        values=[getattr(self,key) for key in required]
        if any(not value or len(value)<32 or 'viio-internal-secret-token' in value for value in values):
            raise ValueError('Production requires distinct service keys of at least 32 characters')
        if len(set(values))!=len(values):
            raise ValueError('Service keys must have distinct values')
        previous=[self.documents_read_key_previous,self.documents_admin_key_previous,self.document_worker_key_previous]
        previous=[value for value in previous if value]
        if any(len(value)<32 or value in values for value in previous) or len(previous)!=len(set(previous)):
            raise ValueError('Previous service keys must be long and distinct')

    def runtime_keys(self):
        return ('document_worker_key',) if self.service_role=='document-worker' else ('document_worker_key','documents_read_key','documents_admin_key','consultant_source_key')
