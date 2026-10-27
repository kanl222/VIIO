"""Оркестратор запуска ASGI сервера Uvicorn для микросервиса assistent."""

from __future__ import annotations

import logging
import uvicorn

from assistent.config import Settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_app(settings: Settings | None = None) -> None:
    """Запуск приложения с использованием Uvicorn на основе настроек Settings.

    Args:
        settings: Опциональный объект конфигурации. Если не передан, считывается из окружения.
    """
    app_settings = settings or Settings()

    logger.info(
        "Запуск assistent сервера на http://%s:%d (модель: %s, Qdrant: %s)",
        app_settings.api_host,
        app_settings.api_port,
        app_settings.ollama_model,
        app_settings.qdrant_url,
    )

    uvicorn.run(
        "assistent:create_app",
        host=app_settings.api_host,
        port=app_settings.api_port,
        factory=True,
    )
