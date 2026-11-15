"""Скрипт импорта и индексации новых документов базы знаний VIIO.

1. Загружает 5 новых структурированных документов из data/gosapteka56/2026-10-27/rag-prepared/
2. Регистрирует их в PostgreSQL (documents, document_contents, document_publications)
3. Индексирует в Qdrant (коллекции knowledge_base_big_rag_20261025 и knowledge_public)
   с поддержкой Small2Big Sentence-Window, Dense векторов и Sparse BM25
4. Обновляет счетчики chunks_count в таблице documents
5. Очищает кэш в Redis
"""

import asyncio
import hashlib
import logging
import os
from pathlib import Path
import sys
import uuid

import psycopg
from psycopg.rows import dict_row
from qdrant_client import QdrantClient, models

# Добавляем путь к viio_rag
sys.path.insert(0, "/app/src")

from viio_rag.chunks import document_chunks
from viio_rag.config import Settings
from viio_rag.embeddings import create_embeddings
from viio_rag.publication_index import PublicationIndex
from viio_rag.sparse_bm25 import get_sparse_encoder
from viio_rag.vector_store import QdrantVectorStoreAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("import_index_kb")

NEW_DOCUMENTS = [
    {
        "id": "doc-kontakty-tehpodderzhki-islo-oas",
        "file": "70_kontakty_i_reglament_tehpodderzhki_is_lo_oas.md",
        "title": "Регламент и контакты технической поддержки ИС «Лекарственное обеспечение» и ГАУЗ «ОАС»",
    },
    {
        "id": "doc-elektronny-recept-karta-orenburzhtsa",
        "file": "71_elektronny_recept_karta_zhitelya_orenburzhtsa.md",
        "title": "Регламент работы с электронными рецептами и «Универсальной картой оренбуржца»",
    },
    {
        "id": "doc-prikazy-mz-rf-1094n-1093n-pravila-otpuska",
        "file": "72_prikazy_mz_rf_1094n_1093n_pravila_naznacheniya_i_otpuska.md",
        "title": "Нормативно-правовое регулирование: Приказы Минздрава России № 1094н, № 1093н, № 947н и № 911н",
    },
    {
        "id": "doc-mdlp-shemy-vybytiya-registrator-vybytiya",
        "file": "73_mdlp_shemy_vybytiya_registrator_vybytiya_10521_10531.md",
        "title": "Регламент МДЛП «Честный Знак»: Регистраторы выбытия, схемы льготного отпуска 10521 и списания 10531",
    },
    {
        "id": "doc-goryachie-linii-lekarstvennogo-obespecheniya-oo",
        "file": "74_goryachie_linii_i_marshruty_obrascheniy_lekarstva_oo.md",
        "title": "Горячие линии и маршрутизация обращений по льготному лекарственному обеспечению в Оренбургской области",
    },
]


async def main():
    pg_host = os.getenv("POSTGRES_HOST", "postgres")
    pg_port = os.getenv("POSTGRES_PORT", "5432")
    pg_user = os.getenv("POSTGRES_USER", "postgres")
    pg_pass = os.getenv("POSTGRES_PASSWORD", "postgres")
    pg_db = os.getenv("POSTGRES_DB", "agent_db")

    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")
    collection_name = os.getenv("QDRANT_COLLECTION_NAME", "knowledge_base_big_rag_20261025")
    public_collection_name = os.getenv("QDRANT_PUBLIC_COLLECTION_NAME", "knowledge_public")

    settings = Settings()
    embeddings = create_embeddings(settings)
    sparse_encoder = get_sparse_encoder()
    qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_api_key, timeout=120)

    dsn = f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"

    logger.info("Проверка моделей эмбеддингов...")
    sample_vec = embeddings.embed_query("проверка")
    logger.info("Dense vector size: %d, Sparse available: %s", len(sample_vec), sparse_encoder.is_available)

    pub_adapter = QdrantVectorStoreAdapter(qdrant, embeddings, public_collection_name)
    pub_index = PublicationIndex(pub_adapter)

    base_dir = Path("/tmp/rag-prepared")
    if not base_dir.exists():
        base_dir = Path("/source/data/gosapteka56/2026-10-27/rag-prepared")
    if not base_dir.exists():
        base_dir = Path("data/gosapteka56/2026-10-27/rag-prepared")

    imported_docs = []

    for item in NEW_DOCUMENTS:
        doc_id = item["id"]
        filename = item["file"]
        title = item["title"]
        filepath = base_dir / filename

        if not filepath.exists():
            raise FileNotFoundError(f"Файл не найден: {filepath}")

        content = filepath.read_text(encoding="utf-8")
        raw_bytes = content.encode("utf-8")
        sha256_hash = hashlib.sha256(raw_bytes).hexdigest()
        file_size = len(raw_bytes)

        logger.info("Обработка документа: %s (%d байт)", title, file_size)

        # 1. Запись в PostgreSQL
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                # Вставка в documents
                storage_key = f"{uuid.uuid4().hex}.md"
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
                        %s, %s, 'admin-import-2026-11-15', 0, 'text/markdown', %s, '{}'::jsonb,
                        NOW(), NOW()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        family_id = EXCLUDED.family_id,
                        title = EXCLUDED.title,
                        original_filename = EXCLUDED.original_filename,
                        is_public = true,
                        allowed_roles = EXCLUDED.allowed_roles,
                        status = 'ready',
                        is_active = true,
                        file_size = EXCLUDED.file_size,
                        sha256 = EXCLUDED.sha256,
                        mime_type = 'text/markdown',
                        updated_at = NOW();
                    """,
                    (doc_id, doc_id, title, filename, file_size, sha256_hash, storage_key),
                )

                # Вставка в document_contents
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

                # Получение или генерация publication_id
                cur.execute("SELECT id FROM document_publications WHERE document_id = %s", (doc_id,))
                pub_row = cur.fetchone()
                if pub_row:
                    pub_id = str(pub_row["id"])
                else:
                    pub_id = str(uuid.uuid4())

                # Вставка в document_publications
                cur.execute(
                    """
                    INSERT INTO document_publications (id, document_id, title, content, publisher_id, published_at)
                    VALUES (%s, %s, %s, %s, 'admin-import-2026-11-15', NOW())
                    ON CONFLICT (document_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        content = EXCLUDED.content,
                        published_at = NOW();
                    """,
                    (pub_id, doc_id, title, content),
                )
            conn.commit()

        # 2. Индексация в knowledge_base_big_rag_20261025 (Small2Big + BM25)
        # Сначала удалим старые чанки данного документа (если были)
        qdrant.delete(
            collection_name=collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="metadata.document_id", match=models.MatchValue(value=doc_id))]
                )
            ),
            wait=True,
        )

        doc_parts = list(document_chunks(content, size=500, overlap=50, unit="characters"))
        if not doc_parts:
            doc_parts = [(content, {"window_context": content})]

        chunk_texts = [p[0] for p in doc_parts]
        chunk_extras = [p[1] for p in doc_parts]

        dense_vectors = embeddings.embed_documents(chunk_texts)
        sparse_vectors = sparse_encoder.embed_documents(chunk_texts)

        points = []
        for c_idx, (text, extra, d_vec, s_vec) in enumerate(zip(chunk_texts, chunk_extras, dense_vectors, sparse_vectors)):
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{c_idx}"))
            vec_dict = {"": d_vec}
            if s_vec.indices:
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
                    "chunk_size": 500,
                    "chunk_overlap": 50,
                    "is_public": True,
                    "allowed_roles": ["employee", "programmer", "admin"],
                    "allowed_departments": [],
                    "owner_id": "admin-import-2026-11-15",
                    "version": 1,
                    "is_active": True,
                    "ingestion_ready": True,
                    "window_context": extra.get("window_context", text),
                },
            }
            points.append(models.PointStruct(id=point_id, vector=vec_dict, payload=payload))

        qdrant.upsert(collection_name=collection_name, points=points, wait=True)

        # Обновляем chunks_count в PostgreSQL
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE documents SET chunks_count = %s, updated_at = NOW() WHERE id = %s", (len(points), doc_id))
            conn.commit()

        # 3. Публикация в knowledge_public для Consultant
        await pub_index.put({"id": pub_id, "document_id": doc_id, "title": title, "content": content})

        imported_docs.append((doc_id, title, len(points)))
        logger.info("Успешно проиндексирован: '%s' -> %d чанков", title, len(points))

    # 4. Очистка кэша Redis
    try:
        import redis.asyncio as aioredis
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        r = aioredis.from_url(redis_url)
        keys = await r.keys("viio:*")
        if keys:
            await r.delete(*keys)
            logger.info("Очищено %d ключей кэша в Redis.", len(keys))
        await r.aclose()
    except Exception as e:
        logger.warning("Не удалось очистить Redis: %s", e)

    logger.info("=== ИМПОРТ И ИНДЕКСАЦИЯ УСПЕШНО ЗАВЕРШЕНЫ ===")
    for doc_id, title, chunks in imported_docs:
        logger.info("- %s: %s (%d чанков)", doc_id, title, chunks)


if __name__ == "__main__":
    asyncio.run(main())
