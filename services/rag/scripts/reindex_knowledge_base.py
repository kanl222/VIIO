"""Скрипт переиндексации базы знаний VIIO: Small2Big + Sparse BM25 + Qdrant.

Считывает все готовые документы из PostgreSQL (таблицы documents и document_contents),
нарезает на смысловые фрагменты с вычислением окна предложений Small2Big,
генерирует плотные эмбеддинги (Dense) и разреженные BM25 векторы (FastEmbed),
пересоздаёт коллекцию Qdrant с конфигурацией sparse_vectors_config={'bm25': ...}
и загружает данные с полной структурой метаданных и связным контекстом.
"""

import asyncio
import logging
import os
from pathlib import Path
import sys
import uuid

from dotenv import dotenv_values
import psycopg
from psycopg.rows import dict_row
from qdrant_client import QdrantClient, models

# Добавляем пути к исходникам
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "RAG" / "src"))

from viio_rag.application.chunks import document_chunks
from viio_rag.config import Settings
from viio_rag.infrastructure.models.embeddings import create_embeddings
from viio_rag.infrastructure.models.sparse_bm25 import get_sparse_encoder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reindex_kb")


async def main():
    env_file = ROOT / ".env"
    env_values = {**dotenv_values(env_file), **os.environ} if env_file.exists() else os.environ

    # Параметры подключения
    qdrant_url = env_values.get("QDRANT_URL", "http://127.0.0.1:6333")
    qdrant_api_key = env_values.get("QDRANT_API_KEY")
    collection_name = env_values.get("QDRANT_COLLECTION_NAME", "knowledge_base_big_rag_20261025")
    public_collection_name = env_values.get("QDRANT_PUBLIC_COLLECTION_NAME", "knowledge_public")

    pg_host = env_values.get("POSTGRES_HOST", "127.0.0.1")
    if pg_host in ("postgres", "host.docker.internal"):
        pg_host = "127.0.0.1"
    pg_port = env_values.get("POSTGRES_PORT", "5432")
    pg_user = env_values.get("POSTGRES_USER", "postgres")
    pg_pass = env_values.get("POSTGRES_PASSWORD", "postgres")
    pg_db = "agent_db"

    # Настройки эмбеддингов: локально обращаемся к 127.0.0.1 вместо host.docker.internal
    lm_url = env_values.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
    if "host.docker.internal" in lm_url:
        lm_url = lm_url.replace("host.docker.internal", "127.0.0.1")

    settings = Settings(
        lmstudio_base_url=lm_url,
        lmstudio_api_key=env_values.get("LMSTUDIO_API_KEY", "lm-studio"),
        lmstudio_embedding_model=env_values.get("LMSTUDIO_EMBEDDING_MODEL", "text-embedding-nomic-embed-text-v1.5"),
        qdrant_url=qdrant_url,
        qdrant_api_key=qdrant_api_key,
        qdrant_collection_name=collection_name,
        qdrant_public_collection_name=public_collection_name,
    )

    logger.info("Инициализация моделей...")
    embeddings = create_embeddings(settings)
    sparse_encoder = get_sparse_encoder()
    if not sparse_encoder.is_available:
        raise RuntimeError("FastEmbed Sparse BM25 encoder недоступен!")

    # Проверка плотных эмбеддингов
    sample_vec = embeddings.embed_query("проверка подключения")
    vector_size = len(sample_vec)
    logger.info("Плотные эмбеддинги готовы, размерность: %d", vector_size)

    # Подключение к Qdrant
    qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_api_key, timeout=120)

    # Пересоздание коллекции Qdrant с dense и sparse векторами
    logger.info("Пересоздание коллекции Qdrant: %s", collection_name)
    if qdrant.collection_exists(collection_name):
        qdrant.delete_collection(collection_name)

    qdrant.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
        sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    logger.info("Коллекция %s создана с поддержкой BM25 sparse vectors!", collection_name)

    # Чтение документов из PostgreSQL
    dsn = f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"
    logger.info("Чтение документов из базы данных %s...", pg_db)

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT d.id, d.title, d.original_filename, d.document_kind, d.is_public,
                       d.allowed_roles, d.allowed_departments, d.owner_id, d.version,
                       c.content
                FROM documents d
                JOIN document_contents c ON d.id = c.document_id
                WHERE d.status = 'ready' AND d.is_active = true
                ORDER BY d.created_at ASC
            """)
            documents = cur.fetchall()

    logger.info("Найдено %d активных документов для индексации", len(documents))

    total_chunks = 0
    updated_counts = []

    for doc_idx, doc in enumerate(documents, 1):
        doc_id = doc["id"]
        title = doc["title"]
        content = doc["content"]
        filename = doc["original_filename"] or ""
        roles = doc["allowed_roles"] or ["employee"]
        depts = doc["allowed_departments"] or ["general"]
        is_pub = bool(doc["is_public"])
        owner_id = doc["owner_id"] or ""
        version = doc["version"] or 1
        kind = doc["document_kind"] or "document"

        # Нарезка на чанки с контекстным окном Small2Big
        doc_parts = list(document_chunks(content, size=500, overlap=50, unit="characters"))
        if not doc_parts:
            doc_parts = [(content, {"window_context": content})]

        chunk_texts = [p[0] for p in doc_parts]
        chunk_extras = [p[1] for p in doc_parts]

        # Вычисление плотных векторов батчами по 32
        dense_vectors = []
        batch_size = 32
        for b_start in range(0, len(chunk_texts), batch_size):
            batch = chunk_texts[b_start : b_start + batch_size]
            dense_vectors.extend(embeddings.embed_documents(batch))

        # Вычисление разреженных BM25 векторов
        sparse_vectors = sparse_encoder.embed_documents(chunk_texts)

        # Сборка точек для Qdrant
        points = []
        for c_idx, (text, extra, d_vec, s_vec) in enumerate(zip(chunk_texts, chunk_extras, dense_vectors, sparse_vectors)):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{c_idx}"))
            vec_dict = {
                "": d_vec,
            }
            if s_vec.indices:
                vec_dict["bm25"] = models.SparseVector(indices=s_vec.indices, values=s_vec.values)

            payload = {
                "page_content": text,
                "metadata": {
                    "document_id": doc_id,
                    "title": title,
                    "filename": filename,
                    "kind": kind,
                    "chunk_index": c_idx,
                    "chunk_unit": "characters",
                    "chunk_size": 500,
                    "chunk_overlap": 50,
                    "is_public": is_pub,
                    "allowed_roles": roles,
                    "allowed_departments": depts,
                    "owner_id": owner_id,
                    "version": version,
                    "is_active": True,
                    "ingestion_ready": True,
                    "window_context": extra.get("window_context", text),
                },
            }
            points.append(models.PointStruct(id=point_id, vector=vec_dict, payload=payload))

        # Загрузка точек в Qdrant батчами по 100
        for p_start in range(0, len(points), 100):
            qdrant.upsert(collection_name=collection_name, points=points[p_start : p_start + 100], wait=True)

        total_chunks += len(points)
        updated_counts.append((len(points), doc_id))
        logger.info(
            "[%d/%d] Проиндексирован: '%s' (doc_id=%s) -> %d чанков (Small2Big + BM25)",
            doc_idx,
            len(documents),
            title[:40],
            doc_id[:12],
            len(points),
        )

    # Обновление chunks_count в PostgreSQL
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for count, doc_id in updated_counts:
                cur.execute("UPDATE documents SET chunks_count = %s, updated_at = NOW() WHERE id = %s", (count, doc_id))
        conn.commit()

    logger.info("Обновлены счётчики chunks_count в таблице documents PostgreSQL.")

    # Очистка кэша Redis
    try:
        import redis.asyncio as aioredis
        redis_url = env_values.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        r = aioredis.from_url(redis_url)
        keys = await r.keys("viio:*")
        if keys:
            await r.delete(*keys)
            logger.info("Очищено %d ключей кэша в Redis.", len(keys))
        await r.aclose()
    except Exception as e:
        logger.warning("Не удалось очистить Redis: %s", e)

    qdrant_info = qdrant.get_collection(collection_name)
    logger.info("=== ПЕРЕИНДЕКСАЦИЯ УСПЕШНО ЗАВЕРШЕНА ===")
    logger.info("Коллекция: %s", collection_name)
    logger.info("Всего документов: %d", len(documents))
    logger.info("Всего фрагментов в Qdrant: %d", qdrant_info.points_count)
    logger.info("Sparse BM25: настроен и заполнен.")
    logger.info("Small2Big: метаданные window_context сохранены.")


if __name__ == "__main__":
    asyncio.run(main())
