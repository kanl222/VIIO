"""Каталог документов: CRUD, версии, права доступа.

Таблица documents в agent_db хранит метаданные каждого загруженного оригинала
и связывает их с чанками Qdrant через document_id.  Векторный индекс (Qdrant)
остаётся поисковым, но не единственным хранилищем сведений о документе.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)


def normalize_catalog_metadata(metadata):
    metadata = metadata or {}
    tags = metadata.get('tags', [])
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError('Теги должны быть списком строк')
    if len(tags) > 20 or any(len(tag) > 100 for tag in tags):
        raise ValueError('Допускается до 20 тегов длиной до 100 символов')
    return {'description': str(metadata.get('description', ''))[:500],
            'tags': sorted({tag.strip().lower() for tag in tags if tag.strip()})}

# ---------------------------------------------------------------------------
# DDL — выполняется при первом старте приложения
# ---------------------------------------------------------------------------

CATALOG_DDL = """
CREATE TABLE IF NOT EXISTS documents (
    id                  VARCHAR(64)  PRIMARY KEY,
    title               VARCHAR(255) NOT NULL,
    mime_type           VARCHAR(128) NOT NULL DEFAULT 'text/plain',
    file_size           BIGINT,
    sha256              VARCHAR(64),
    owner_id            VARCHAR(64),
    version             INTEGER      NOT NULL DEFAULT 1,
    parent_id           VARCHAR(64)  REFERENCES documents(id) ON DELETE SET NULL,
    status              VARCHAR(32)  NOT NULL DEFAULT 'indexed',
    is_public           BOOLEAN      NOT NULL DEFAULT FALSE,
    allowed_roles       TEXT[]       NOT NULL DEFAULT ARRAY['employee']::TEXT[],
    allowed_departments TEXT[]       NOT NULL DEFAULT ARRAY['general']::TEXT[],
    storage_key         VARCHAR(512),
    chunks_count        INTEGER      NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_documents_owner      ON documents(owner_id);
CREATE INDEX IF NOT EXISTS idx_documents_status     ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_created_at ON documents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_documents_sha256     ON documents(sha256) WHERE sha256 IS NOT NULL;
"""


# ---------------------------------------------------------------------------
# Доменные модели
# ---------------------------------------------------------------------------

from viio_rag.records import DocumentRecord


@dataclass
class DocumentListResult:
    """Результат постраничного запроса к каталогу."""

    items: list[DocumentRecord]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Сервис каталога
# ---------------------------------------------------------------------------

class DocumentCatalogService:
    """CRUD-сервис каталога документов.

    Работает через AsyncConnectionPool (psycopg 3).  Не зависит от Qdrant —
    это слой PostgreSQL поверх векторного индекса.

    Args:
        pool: Открытый пул асинхронных соединений к agent_db.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @property
    def pool(self):
        if self._pool is None:
            raise RuntimeError("Каталог документов недоступен")
        return self._pool

    @property
    def available(self):
        return self._pool is not None

    def set_pool(self, pool):
        self._pool = pool

    # ------------------------------------------------------------------
    # Инициализация схемы
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """Создать таблицу, если она не существует."""
        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            await conn.execute('SELECT pg_advisory_xact_lock(hashtext(current_schema()), 78232)')
            await conn.execute(CATALOG_DDL)
            await conn.execute('ALTER TABLE documents ADD COLUMN IF NOT EXISTS family_id varchar(64)')
            await conn.execute('ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true')
            await conn.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS document_kind text NOT NULL DEFAULT 'document'")
            await conn.execute('ALTER TABLE documents ADD COLUMN IF NOT EXISTS original_filename varchar(255)')
            await conn.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS catalog_metadata jsonb NOT NULL DEFAULT '{}'::jsonb")
            await conn.execute('UPDATE documents SET family_id=id WHERE family_id IS NULL')
            await conn.execute('ALTER TABLE documents ALTER COLUMN family_id SET NOT NULL')
            await conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS documents_family_version ON documents(family_id,version)')
            await conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS documents_one_active ON documents(family_id) WHERE is_active')
            from viio_rag.knowledge_data import KNOWLEDGE_DDL
            await conn.execute(KNOWLEDGE_DDL)

        logger.info("Каталог документов: схема инициализирована")

    # ------------------------------------------------------------------
    # Запись
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        doc_id: str,
        title: str,
        chunks_count: int,
        mime_type: str = "text/plain",
        file_size: int | None = None,
        sha256: str | None = None,
        owner_id: str | None = None,
        is_public: bool = False,
        allowed_roles: list[str] | None = None,
        allowed_departments: list[str] | None = None,
        storage_key: str | None = None,
        status: str = "indexed",
        family_id: str | None = None,
        version: int = 1,
        parent_id: str | None = None,
        is_active: bool = True,
        document_kind: str = 'document',
        original_filename: str | None = None,
        catalog_metadata: dict | None = None,
    ) -> DocumentRecord:
        """Создать новую запись в каталоге."""
        roles = allowed_roles if allowed_roles is not None else ["employee"]
        depts = allowed_departments if allowed_departments is not None else ["general"]

        row: dict[str, Any]
        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                """
                INSERT INTO documents
                    (id, title, mime_type, file_size, sha256, owner_id,
                     is_public, allowed_roles, allowed_departments,
                     storage_key, chunks_count, status,family_id,version,parent_id,is_active,document_kind,original_filename,catalog_metadata)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(id) DO UPDATE SET storage_key=EXCLUDED.storage_key,
                    status=EXCLUDED.status,chunks_count=EXCLUDED.chunks_count,updated_at=now()
                RETURNING *
                """,
                (
                    doc_id, title, mime_type, file_size, sha256, owner_id,
                    is_public, roles, depts,
                    storage_key, chunks_count, status,
                    family_id or doc_id, version, parent_id, is_active,
                    document_kind,
                    original_filename,
                    Jsonb(normalize_catalog_metadata(catalog_metadata)),
                ),
            )
            row = await cur.fetchone()  # type: ignore[assignment]

        logger.info("Каталог: создан документ %s (%s)", doc_id, title)
        return DocumentRecord.from_row(row)

    async def update_storage_key(self, doc_id: str, storage_key: str) -> None:
        """Сохранить путь к оригиналу после загрузки файла."""
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE documents SET storage_key=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                (storage_key, doc_id),
            )

    async def update_status(self, doc_id: str, status: str) -> None:
        """Обновить статус документа (queued / processing / ready / error)."""
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE documents SET status=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                (status, doc_id),
            )

    async def update_chunks_count(self, doc_id: str, chunks_count: int) -> None:
        """Обновить число чанков после индексации."""
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE documents SET chunks_count=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                (chunks_count, doc_id),
            )

    async def delete(self, doc_id: str) -> bool:
        """Удалить документ. Возвращает True, если запись существовала."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "DELETE FROM documents WHERE id=%s RETURNING id", (doc_id,)
            )
            deleted = await cur.fetchone()
        if deleted:
            logger.info("Каталог: удалён документ %s", doc_id)
        return deleted is not None

    # ------------------------------------------------------------------
    # Чтение
    # ------------------------------------------------------------------

    async def get(self, doc_id: str) -> DocumentRecord | None:
        """Получить одну запись по id."""
        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                "SELECT * FROM documents WHERE id=%s", (doc_id,)
            )
            row = await cur.fetchone()
        return DocumentRecord.from_row(row) if row else None

    async def find_by_sha256(
        self, sha256: str, *, owner_id: str | None = None
    ) -> DocumentRecord | None:
        """Найти документ по хешу (обнаружение дубликата в пределах прав владельца)."""
        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            if owner_id:
                cur = await conn.execute(
                    "SELECT * FROM documents WHERE sha256=%s AND owner_id=%s LIMIT 1",
                    (sha256, owner_id),
                )
            else:
                cur = await conn.execute(
                    "SELECT * FROM documents WHERE sha256=%s LIMIT 1", (sha256,)
                )
            row = await cur.fetchone()
        return DocumentRecord.from_row(row) if row else None

    async def list_documents(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        owner_id: str | None = None,
    ) -> DocumentListResult:
        """Постраничный список документов с необязательными фильтрами."""
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        offset = (page - 1) * page_size

        conditions: list[str] = ['is_active = TRUE']
        params: list[Any] = []
        idx = 1

        if status is not None:
            conditions.append("status = %s")
            params.append(status)
            idx += 1
        if owner_id is not None:
            conditions.append("owner_id = %s")
            params.append(owner_id)
            idx += 1

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            count_cur = await conn.execute(
                f"SELECT COUNT(*)::int AS cnt FROM documents {where}", params
            )
            total_row = await count_cur.fetchone()
            total = total_row["cnt"] if total_row else 0

            rows_cur = await conn.execute(
                f"""
                SELECT * FROM documents {where}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [page_size, offset],
            )
            rows = await rows_cur.fetchall()

        return DocumentListResult(
            items=[DocumentRecord.from_row(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def count(self) -> int:
        """Общее число записей в каталоге (документы, не чанки)."""
        async with self._pool.connection() as conn:
            cur = await conn.execute("SELECT COUNT(*)::int AS cnt FROM documents WHERE is_active")
            row = await cur.fetchone()
        return row["cnt"] if isinstance(row, dict) else row[0] if row else 0

    async def update_catalog_metadata(self, doc_id, metadata):
        normalized = normalize_catalog_metadata(metadata)
        value = {key: normalized[key] for key in metadata}
        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            row = await (await conn.execute(
                "UPDATE documents SET catalog_metadata=catalog_metadata || %s, updated_at=now() WHERE id=%s RETURNING *",
                (Jsonb(value), doc_id))).fetchone()
        return DocumentRecord.from_row(row) if row else None

    async def versions(self, family_id):
        async with self.pool.connection() as conn:
            conn.row_factory = dict_row
            rows = await (await conn.execute('SELECT * FROM documents WHERE family_id=%s ORDER BY version DESC', (family_id,))).fetchall()
            return [DocumentRecord.from_row(row) for row in rows]

    async def activate_version(self, identifier, parent_id):
        async with self.pool.connection() as conn:
            cursor = await conn.execute("UPDATE documents SET is_active=false,status='superseded',updated_at=now() WHERE id=%s AND is_active RETURNING id", (parent_id,))
            if not await cursor.fetchone():
                raise ValueError('Актуальная версия изменилась. Выберите документ заново.')
            await conn.execute('DELETE FROM document_publications WHERE document_id=%s', (parent_id,))
            activated = await conn.execute("UPDATE documents SET is_active=true,status='ready',updated_at=now() WHERE id=%s AND family_id=(SELECT family_id FROM documents WHERE id=%s) AND NOT is_active RETURNING id", (identifier, parent_id))
            if not await activated.fetchone():
                raise ValueError('Подготовленная версия не найдена')

    async def pending_version_families(self):
        async with self.pool.connection() as conn:
            conn.row_factory = dict_row
            rows = await (await conn.execute("SELECT DISTINCT family_id FROM documents WHERE version>1 AND status='processing'")).fetchall()
            return [row['family_id'] for row in rows]


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def sha256_of_bytes(data: bytes) -> str:
    """SHA-256 хеш произвольного байтового содержимого."""
    return hashlib.sha256(data).hexdigest()


def sha256_of_text(text: str) -> str:
    """SHA-256 хеш текстового содержимого (UTF-8)."""
    return hashlib.sha256(text.encode()).hexdigest()
