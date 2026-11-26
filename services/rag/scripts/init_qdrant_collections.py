"""Automatic initialization of Qdrant collections on cold start.

Pre-creates collections with dense and sparse BM25 vectors so application containers
(document-worker, documents, rag, assistent) do not encounter 404 Not Found on cold start.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from dotenv import dotenv_values
from qdrant_client import QdrantClient, models

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("init_qdrant")


def get_env_config() -> dict[str, str]:
    root = Path(__file__).resolve().parents[3]
    env_file = root / ".env"
    values = {}
    if env_file.exists():
        values.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    values.update({k: v for k, v in os.environ.items() if v is not None})
    return values


def init_collections() -> None:
    env = get_env_config()
    qdrant_url = env.get("QDRANT_URL", "http://localhost:6333")
    # If running on host and URL points to internal docker hostname 'qdrant', swap to localhost
    if "://qdrant:" in qdrant_url:
        qdrant_url = qdrant_url.replace("://qdrant:", "://localhost:")

    api_key = env.get("QDRANT_API_KEY")
    if not api_key:
        logger.warning("QDRANT_API_KEY не задан в .env или окружении")

    logger.info("Подключение к Qdrant по адресу: %s", qdrant_url)
    client = QdrantClient(url=qdrant_url, api_key=api_key, timeout=15)

    # 1. Ожидание готовности Qdrant
    ready = False
    for attempt in range(1, 31):
        try:
            client.get_collections()
            ready = True
            logger.info("Qdrant доступен (попытка %d)", attempt)
            break
        except Exception as exc:
            logger.debug("Ожидание Qdrant (%d/30): %s", attempt, exc)
            time.sleep(1)

    if not ready:
        logger.error("Qdrant недоступен после 30 секунд ожидания!")
        sys.exit(1)

    # 2. Определение размерности эмбеддингов
    vector_size = 768  # Надежное значение по умолчанию для nomic-embed-text
    try:
        from viio_rag.config import Settings
        from viio_rag.infrastructure.models.embeddings import create_embeddings

        s = Settings()
        embeddings = create_embeddings(s)
        sample = embeddings.embed_query("ping")
        if sample and len(sample) > 0:
            vector_size = len(sample)
            logger.info("Размерность векторов определена через модель эмбеддингов: %d", vector_size)
    except Exception as exc:
        logger.info(
            "Не удалось опросить модель эмбеддингов (%s), используется размерность по умолчанию %d",
            exc,
            vector_size,
        )

    # 3. Список обязательных коллекций
    col_main = env.get("QDRANT_COLLECTION_NAME", "knowledge_base")
    col_public = env.get("QDRANT_PUBLIC_COLLECTION_NAME", "knowledge_public")
    col_project = "project_memory"

    collections_to_ensure = [col_main, col_public, col_project]

    for col in collections_to_ensure:
        try:
            if client.collection_exists(col):
                logger.info("Коллекция '%s' уже существует в Qdrant.", col)
                continue

            logger.info(
                "Создание коллекции '%s' (размерность=%d, BM25 modifier=IDF)...", col, vector_size
            )
            client.create_collection(
                collection_name=col,
                vectors_config=models.VectorParams(
                    size=vector_size, distance=models.Distance.COSINE
                ),
                sparse_vectors_config={
                    "bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)
                },
            )
            for _ in range(10):
                if client.collection_exists(col):
                    break
                time.sleep(0.5)
            logger.info("Коллекция '%s' успешно инициализирована.", col)
        except Exception as exc:
            if "already exists" in str(exc).lower():
                logger.info("Коллекция '%s' уже создана параллельным процессом.", col)
            else:
                logger.error("Ошибка при создании коллекции '%s': %s", col, exc)
                raise

    logger.info("Все коллекции Qdrant успешно проверены и готовы к работе.")


if __name__ == "__main__":
    init_collections()
