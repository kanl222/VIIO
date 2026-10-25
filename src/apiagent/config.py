"""Модуль конфигурации микросервиса APIAgent.

Содержит настройки приложения, считываемые из переменных окружения и .env файлов
на основе Pydantic Settings. Поддерживает работу с LM Studio и Ollama.
"""

from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки конфигурации сервиса APIAgent.

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
