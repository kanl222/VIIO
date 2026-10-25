"""Модуль конфигурации микросервиса APIAgent.

Содержит настройки приложения, считываемые из переменных окружения и .env файлов
на основе Pydantic Settings.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки конфигурации сервиса APIAgent.

    Attributes:
        ollama_base_url: Базовый URL для подключения к сервису Ollama.
        ollama_model: Имя модели LLM для генерации ответов.
        ollama_embedding_model: Имя модели для формирования эмбеддингов.
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
