"""Скрипт импорта всех подготовленных документов RAG в PostgreSQL, Qdrant и Memgraph.

Импортирует все документы из data/gosapteka56/2026-10-27/rag-prepared:
1. Записывает метаданные и содержимое в PostgreSQL (documents, document_contents, document_publications).
2. Нарезает текст на фрагменты (Small2Big Sentence-Window).
3. Создаёт плотные (Nomic 768d) и разреженные (FastEmbed BM25) векторы.
4. Загружает в Qdrant (knowledge_base_big_rag_20261025 и knowledge_public).
5. Связывает документы в графе Memgraph (узел Document).
6. Очищает кэш в Redis.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
import re
import sys
import time
import uuid

from dotenv import dotenv_values
from neo4j import GraphDatabase
import psycopg
from psycopg.rows import dict_row
from qdrant_client import QdrantClient, models

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "rag" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "viio-core" / "src"))

from viio_rag.chunks import document_chunks
from viio_rag.config import Settings
from viio_rag.embeddings import create_embeddings
from viio_rag.sparse_bm25 import get_sparse_encoder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest_rag_prepared")


def extract_title(content: str, default_name: str) -> str:
    """Извлекает заголовок из первой строки Markdown (# Заголовок)."""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# "):
            title = line[2:].strip()
            # Убираем кавычки или спецсимволы по краям
            return title.strip("`*\"'")
    clean_name = default_name.replace(".md", "").replace("_", " ")
    return clean_name.capitalize()


async def main():
    env_file = ROOT / ".env"
    env_values = {**dotenv_values(env_file), **os.environ} if env_file.exists() else os.environ

    input_dir = ROOT / "data" / "gosapteka56" / "2026-10-27" / "rag-prepared"
    if not input_dir.exists():
        raise FileNotFoundError(f"Каталог не найден: {input_dir}")

    files = sorted(input_dir.glob("*.md"))
    if not files:
        raise ValueError(f"В каталоге {input_dir} не найдено .md файлов!")

    logger.info("Найдено %d файлов для индексации в %s", len(files), input_dir)

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
    dsn = f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"

    # Настройки эмбеддингов
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

    logger.info("Подготовка моделей: плотные эмбеддинги (%s)...", settings.lmstudio_embedding_model)
    embeddings = create_embeddings(settings)
    sparse_encoder = get_sparse_encoder()
    if not sparse_encoder.is_available:
        logger.warning("Sparse BM25 encoder недоступен, будет использован только плотный вектор")

    sample_vec = embeddings.embed_query("тестовый запрос")
    vector_size = len(sample_vec)
    logger.info("Плотные эмбеддинги готовы, размерность: %d", vector_size)

    # Инициализация Qdrant клиента
    qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_api_key, timeout=120)

    # Создание или пересоздание коллекций
    for coll in [collection_name, public_collection_name]:
        if not qdrant.collection_exists(coll):
            logger.info("Создание коллекции Qdrant: %s", coll)
            qdrant.create_collection(
                collection_name=coll,
                vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
                sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)} if sparse_encoder.is_available else None,
            )
        else:
            logger.info("Коллекция Qdrant %s уже существует.", coll)

    # Инициализация Memgraph
    memgraph_driver = None
    try:
        memgraph_driver = GraphDatabase.driver("bolt://127.0.0.1:7687", auth=("", ""))
        logger.info("Подключение к Memgraph установлено.")
    except Exception as exc:
        logger.warning("Не удалось подключиться к Memgraph: %s", exc)

    total_chunks = 0
    t0 = time.time()

    for idx, filepath in enumerate(files, 1):
        filename = filepath.name
        content = filepath.read_text(encoding="utf-8")
        title = extract_title(content, filename)
        if len(title) > 250:
            title = title[:247] + "..."
        stem = filepath.stem.replace('_', '-')
        if len(stem) > 55:
            stem = stem[:55].rstrip('-')
        doc_id = f"doc-{stem}"
        raw_bytes = content.encode("utf-8")
        sha256_hash = hashlib.sha256(raw_bytes).hexdigest()
        file_size = len(raw_bytes)

        # 1. Запись в PostgreSQL
        storage_key = f"{uuid.uuid4().hex}.md"
        pub_id = str(uuid.uuid4())
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO documents (
                        id, family_id, title, original_filename, document_kind, is_public,
                        allowed_roles, allowed_departments, version, status, is_active,
                        file_size, sha256, owner_id, chunks_count, mime_type, storage_key, catalog_metadata,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, 'document', true,
                        ARRAY['employee', 'programmer', 'admin']::text[], ARRAY[]::text[], 1, 'ready', true,
                        %s, %s, 'admin-import-rag', 0, 'text/markdown', %s, '{}'::jsonb,
                        NOW(), NOW()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        family_id = EXCLUDED.family_id,
                        title = EXCLUDED.title,
                        original_filename = EXCLUDED.original_filename,
                        is_public = true,
                        status = 'ready',
                        is_active = true,
                        file_size = EXCLUDED.file_size,
                        sha256 = EXCLUDED.sha256,
                        mime_type = 'text/markdown',
                        updated_at = NOW();
                    """,
                    (doc_id, doc_id, title, filename, file_size, sha256_hash, storage_key),
                )

                cur.execute(
                    """
                    INSERT INTO document_contents (document_id, content, filename, title)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (document_id) DO UPDATE SET
                        content = EXCLUDED.content,
                        filename = EXCLUDED.filename,
                        title = EXCLUDED.title;
                    """,
                    (doc_id, content, filename, title),
                )

                cur.execute(
                    """
                    INSERT INTO document_publications (id, document_id, title, content, publisher_id, published_at)
                    VALUES (%s, %s, %s, %s, 'admin-import-rag', NOW())
                    ON CONFLICT (document_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        content = EXCLUDED.content,
                        published_at = NOW();
                    """,
                    (pub_id, doc_id, title, content),
                )
            conn.commit()

        # 2. Нарезка на чанки
        doc_parts = list(document_chunks(content, size=800, overlap=100, unit="characters"))
        if not doc_parts:
            doc_parts = [(content, {"window_context": content})]

        chunk_texts = [p[0] for p in doc_parts]
        chunk_extras = [p[1] for p in doc_parts]

        # 3. Вычисление эмбеддингов
        dense_vectors = []
        batch_size = 64
        for b_start in range(0, len(chunk_texts), batch_size):
            batch = chunk_texts[b_start : b_start + batch_size]
            dense_vectors.extend(embeddings.embed_documents(batch))

        sparse_vectors = sparse_encoder.embed_documents(chunk_texts) if sparse_encoder.is_available else [None] * len(chunk_texts)

        # 4. Формирование точек Qdrant
        points = []
        for c_idx, (text, extra, d_vec, s_vec) in enumerate(zip(chunk_texts, chunk_extras, dense_vectors, sparse_vectors)):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{c_idx}"))
            vec_dict = {"": d_vec}
            if s_vec is not None and getattr(s_vec, "indices", None):
                vec_dict["bm25"] = models.SparseVector(indices=s_vec.indices, values=s_vec.values)

            payload = {
                "page_content": text,
                "metadata": {
                    "document_id": doc_id,
                    "title": title,
                    "filename": filename,
                    "kind": "document",
                    "chunk_index": c_idx,
                    "chunk_unit": "characters",
                    "chunk_size": 800,
                    "chunk_overlap": 100,
                    "is_public": True,
                    "allowed_roles": ["employee", "programmer", "admin"],
                    "allowed_departments": [],
                    "owner_id": "admin-import-rag",
                    "version": 1,
                    "is_active": True,
                    "ingestion_ready": True,
                    "window_context": extra.get("window_context", text),
                },
            }
            points.append(models.PointStruct(id=point_id, vector=vec_dict, payload=payload))

        # Загрузка точек в Qdrant
        for p_start in range(0, len(points), 100):
            batch_points = points[p_start : p_start + 100]
            qdrant.upsert(collection_name=collection_name, points=batch_points, wait=True)
            qdrant.upsert(collection_name=public_collection_name, points=batch_points, wait=True)

        # 5. Обновление chunks_count в PostgreSQL
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE documents SET chunks_count = %s, updated_at = NOW() WHERE id = %s", (len(points), doc_id))
            conn.commit()

        # 6. Регистрация в Memgraph
        if memgraph_driver is not None:
            try:
                with memgraph_driver.session() as m_sess:
                    m_sess.run(
                        """
                        MERGE (d:Document {id: $doc_id})
                        SET d.title = $title,
                            d.filename = $filename,
                            d.chunks_count = $chunks_count,
                            d.is_public = true
                        """,
                        doc_id=doc_id,
                        title=title,
                        filename=filename,
                        chunks_count=len(points),
                    )
            except Exception as m_exc:
                logger.warning("Ошибка записи документа %s в Memgraph: %s", doc_id, m_exc)

        total_chunks += len(points)
        elapsed = time.time() - t0
        speed = total_chunks / elapsed if elapsed > 0 else 0
        logger.info(
            "[%d/%d] %s -> %d чанков (всего: %d, %.1f чанков/сек)",
            idx,
            len(files),
            title[:45],
            len(points),
            total_chunks,
            speed,
        )

    if memgraph_driver is not None:
        # Связываем документы с сущностями онтологии (ИСЛО, МДЛП и т.д.) по ключевым совпадениям в названиях
        logger.info("Создание связей Document -> System / Abbreviation в Memgraph...")
        try:
            with memgraph_driver.session() as m_sess:
                m_sess.run(
                    """
                    MATCH (d:Document)
                    WHERE d.title CONTAINS 'ИС ЛО' OR d.title CONTAINS 'Лекарственное обеспечение'
                    MATCH (s:System {name: 'ИС «Лекарственное обеспечение»'})
                    MERGE (d)-[:DESCRIBES]->(s)
                    """
                )
                m_sess.run(
                    """
                    MATCH (d:Document)
                    WHERE d.title CONTAINS 'МДЛП' OR d.title CONTAINS 'маркировк'
                    MATCH (s:System {name: 'ИС «МДЛП»'})
                    MERGE (d)-[:DESCRIBES]->(s)
                    """
                )
                m_sess.run(
                    """
                    MATCH (d:Document)
                    WHERE d.title CONTAINS 'WMS' OR d.title CONTAINS 'склад'
                    MATCH (s:System {name: 'WMS'})
                    MERGE (d)-[:DESCRIBES]->(s)
                    """
                )
            logger.info("Связи онтологии в Memgraph созданы.")
        except Exception as exc:
            logger.warning("Ошибка связывания в Memgraph: %s", exc)
        memgraph_driver.close()

    # 7. Очистка Redis
    try:
        import redis.asyncio as aioredis
        redis_url = env_values.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        r = aioredis.from_url(redis_url)
        keys = await r.keys("viio:*")
        if keys:
            await r.delete(*keys)
            logger.info("Очищено %d ключей кэша в Redis.", len(keys))
        await r.aclose()
    except Exception as exc:
        logger.warning("Не удалось очистить Redis: %s", exc)

    total_time = time.time() - t0
    logger.info("=== ЗАГРУЗКА В ВЕКТОРНОЕ ХРАНИЛИЩЕ УСПЕШНО ЗАВЕРШЕНА ===")
    logger.info("Всего файлов: %d", len(files))
    logger.info("Всего чанков в Qdrant: %d", total_chunks)
    logger.info("Общее время: %.1f сек (%.1f чанков/сек)", total_time, total_chunks / total_time if total_time > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
