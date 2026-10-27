"""Await every persistence step; failed indexing never becomes a ready job."""

import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from functools import partial

import anyio.to_thread

from apiagent.services.document_catalog import sha256_of_bytes
from apiagent.services.pdf_extraction import PdfExtractionService
from apiagent.services.bulk_queue import BulkUploadTask, PreparedReview

logger = logging.getLogger(__name__)


class DocumentIngestion:
    def __init__(self, document_service, catalog, store, settings):
        self.documents = document_service
        self.catalog = catalog
        self.store = store
        self.settings = settings

    async def prepare_pdf(self, task):
        extracted = await PdfExtractionService(self.settings).extract(task.data)
        return PreparedReview(extracted['content'], extracted['engine'], extracted['pages'])

    @asynccontextmanager
    async def _document_lock(self, identifier):
        key = int.from_bytes(hashlib.sha256(identifier.encode()).digest()[:8], 'big', signed=True)
        async with self.catalog.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute('SELECT pg_advisory_xact_lock(%s)', (key,))
                yield

    async def __call__(self, task, storage_key):
        digest = sha256_of_bytes(task.data)
        identity_fields = [task.owner_id, task.is_public, sorted(task.allowed_roles),
                           sorted(task.allowed_departments), digest]
        if task.reviewed_content is not None:
            identity_fields.append(sha256_of_bytes(task.reviewed_content.encode()))
        identity = json.dumps(identity_fields, ensure_ascii=False)
        identifier = 'doc-' + uuid.uuid5(uuid.NAMESPACE_URL, identity).hex
        async with self._document_lock(identifier):
            existing = await self.catalog.get(identifier)
            if existing and existing.status in {'ready', 'indexed'} and existing.storage_key:
                return existing.id, existing.chunks_count
            return await self._index(task, storage_key, identifier, digest)

    async def _index(self, task, storage_key, identifier, digest):
        if task.mime_type == 'application/pdf':
            if task.reviewed_content is None:
                raise ValueError('PDF требует проверки текста перед индексацией')
            content = task.reviewed_content
        else:
            content = task.data.decode('utf-8-sig')
        if not content.strip() or '\x00' in content or len(content) > self.settings.pdf_max_chars:
            raise ValueError('Пустой, двоичный или слишком большой текст')
        await anyio.to_thread.run_sync(self.documents.delete_document, identifier)
        try:
            result = await anyio.to_thread.run_sync(partial(
                self.documents.index_document, task.title, content,
                {**task.metadata, 'ingestion_ready': False}, task.is_public, task.allowed_roles,
                task.allowed_departments, task.owner_id, document_id=identifier,
            ))
            await self.catalog.create(doc_id=identifier, title=task.title, chunks_count=result.chunks_count,
                mime_type=task.mime_type, file_size=len(task.data), sha256=digest, owner_id=task.owner_id,
                is_public=task.is_public, allowed_roles=task.allowed_roles,
                allowed_departments=task.allowed_departments, storage_key=storage_key, status='processing')
            await anyio.to_thread.run_sync(self.documents.publish_document, identifier)
            await self.catalog.update_status(identifier, 'ready')
            return identifier, result.chunks_count
        except BaseException:
            # The same document ID and point IDs are used on retries.
            try:
                await anyio.to_thread.run_sync(self.documents.delete_document, identifier)
            except Exception:
                logger.exception('Не удалось убрать незавершённый индекс %s', identifier)
            raise

    async def index_text(self, request):
        task = BulkUploadTask(uuid.uuid4().hex, request.title + '.txt', request.content.encode('utf-8'),
            request.title, request.owner_id, request.is_public, 'text/plain',
            request.allowed_roles, request.allowed_departments, metadata=request.metadata or {})
        key = await anyio.to_thread.run_sync(partial(self.store.save, task.data, suffix='.txt'))
        try:
            identifier, chunks = await self(task, key)
            record = await self.catalog.get(identifier)
            if record.storage_key != key:
                await anyio.to_thread.run_sync(self.store.delete, key)
        except BaseException:
            await anyio.to_thread.run_sync(self.store.delete, key)
            raise
        from apiagent.interfaces._protocols import DocumentMetadata
        return DocumentMetadata(document_id=identifier, title=record.title, chunks_count=chunks, status='indexed')
