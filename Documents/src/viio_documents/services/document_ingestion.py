"""Await every persistence step; failed indexing never becomes a ready job."""

import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from functools import partial

import anyio.to_thread

from viio_documents.services.document_catalog import sha256_of_bytes
from viio_documents.services.pdf_extraction import PdfExtractionService
from viio_documents.services.bulk_queue import BulkUploadTask, PreparedReview, DocumentVersionConflict
from viio_rag.knowledge_data import KnowledgeData
from viio_documents.services.office_extraction import OfficeExtractionService, OFFICE_MIMES
from viio_documents.services.text_normalization import normalize_document_text

logger = logging.getLogger(__name__)


class DocumentIngestion:
    def __init__(self, document_service, catalog, store, settings, knowledge_data=None):
        self.documents = document_service
        self.catalog = catalog
        self.store = store
        self.settings = settings
        self.data = knowledge_data or KnowledgeData(catalog)

    async def prepare_pdf(self, task):
        extracted = await PdfExtractionService(self.settings, filename=task.filename, job_id=task.task_id).extract(task.data)
        return PreparedReview(extracted['content'], extracted['engine'], extracted['pages'], extracted.get('document'))

    @asynccontextmanager
    async def _document_lock(self, identifier):
        key = int.from_bytes(hashlib.sha256(identifier.encode()).digest()[:8], 'big', signed=True)
        async with self.catalog.pool.connection() as conn:
            async with conn.transaction():
                await conn.execute('SELECT pg_advisory_xact_lock(%s)', (key,))
                yield

    async def __call__(self, task, storage_key):
        if task.kind == 'code':
            task.is_public, task.allowed_roles, task.allowed_departments = False, ['programmer', 'admin'], []
        if task.version_parent_id:
            return await self._version(task, storage_key)
        digest = sha256_of_bytes(task.data)
        identity_fields = [task.owner_id, task.is_public, sorted(task.allowed_roles),
                           sorted(task.allowed_departments), digest]
        if task.kind == 'code':
            identity_fields.extend(['code', task.filename])
        if task.reviewed_content is not None:
            identity_fields.append(sha256_of_bytes(task.reviewed_content.encode()))
        identity = json.dumps(identity_fields, ensure_ascii=False)
        identifier = 'doc-' + uuid.uuid5(uuid.NAMESPACE_URL, identity).hex
        async with self._document_lock(identifier):
            existing = await self.catalog.get(identifier)
            if existing and existing.status in {'ready', 'indexed', 'superseded'} and existing.storage_key:
                return existing.id, existing.chunks_count
            return await self._index(task, storage_key, identifier, digest)

    async def _version(self, task, storage_key):
        parent = await self.catalog.get(task.version_parent_id)
        if parent is None:
            raise DocumentVersionConflict('Документ не найден. Выберите его в каталоге заново.')
        family = parent.family_id or parent.id
        identifier = 'doc-' + uuid.uuid5(uuid.NAMESPACE_URL, family + ':' + task.task_id).hex
        async with self._document_lock(family):
            existing = await self.catalog.get(identifier)
            if existing and existing.status in {'ready', 'superseded'}:
                return existing.id, existing.chunks_count
            parent = await self.catalog.get(task.version_parent_id)
            if parent is None or not parent.is_active or parent.status not in {'ready', 'indexed'}:
                raise DocumentVersionConflict('Актуальная версия изменилась. Выберите документ в каталоге заново.')
            versions = await self.catalog.versions(family)
            # Версии наследуют права доступа и владельца, даже если полезная нагрузка в очереди изменена.
            task.owner_id, task.is_public = parent.owner_id, parent.is_public
            task.title = parent.title
            task.allowed_roles = parent.allowed_roles
            task.allowed_departments = parent.allowed_departments
            task.metadata = {**getattr(parent, 'catalog_metadata', {}), **task.metadata}
            if task.kind == 'code' or parent.document_kind == 'code':
                task.kind = 'code'
                task.is_public, task.allowed_roles, task.allowed_departments = False, ['programmer', 'admin'], []
            return await self._index(task, storage_key, identifier, sha256_of_bytes(task.data),
                                     parent=parent, version=existing.version if existing else max(item.version for item in versions) + 1)

    async def recover_versions(self):
        for family in await self.catalog.pending_version_families():
            async with self._document_lock(family):
                for record in await self.catalog.versions(family):
                    await anyio.to_thread.run_sync(self.documents.set_document_active, record.id, record.is_active)

    async def _index(self, task, storage_key, identifier, digest, *, parent=None, version=1):
        if task.reviewed_content is not None:
            content = task.reviewed_content
        elif task.mime_type == 'application/pdf':
            raise ValueError('PDF требует проверки текста перед индексацией')
        elif task.mime_type in OFFICE_MIMES.values():
            extracted = await OfficeExtractionService(self.settings).extract(task.data, task.filename)
            content = extracted['content']
            task.metadata['extraction_engine'] = extracted['engine']
        else:
            content = task.data.decode('utf-8-sig')
            if '\x00' in content:
                raise ValueError('Двоичный файл нельзя индексировать как текст')
        if (task.kind != 'code' and task.mime_type != 'text/markdown'
                and task.metadata.get('input_format') != 'manual-text'
                and task.metadata.get('extraction_engine') != 'docling-cpu'):
            content = normalize_document_text(content)
            task.metadata['text_normalization'] = 'prose-v1'
        if not content.strip() or '\x00' in content or len(content) > self.settings.pdf_max_chars:
            raise ValueError('Пустой, двоичный или слишком большой текст')
        await anyio.to_thread.run_sync(self.documents.delete_document, identifier)
        activation_started = False
        try:
            result = await anyio.to_thread.run_sync(partial(
                self.documents.index_document, task.title, content,
                {**task.metadata, 'kind': task.kind, 'filename': task.filename, 'ingestion_ready': False, 'is_active': parent is None, 'version': version}, task.is_public, task.allowed_roles,
                task.allowed_departments, task.owner_id, document_id=identifier,
            ))
            await self.catalog.create(doc_id=identifier, title=task.title, chunks_count=result.chunks_count,
                mime_type=task.mime_type, file_size=len(task.data), sha256=digest, owner_id=task.owner_id,
                is_public=task.is_public, allowed_roles=task.allowed_roles,
                allowed_departments=task.allowed_departments, storage_key=storage_key, status='processing',
                family_id=(parent.family_id or parent.id) if parent else identifier,
                version=version, parent_id=parent.id if parent else None, is_active=parent is None, document_kind=task.kind,
                original_filename=task.filename[:255], catalog_metadata=task.metadata)
            await self.data.save_text(identifier, content, task.filename, task.title)
            await anyio.to_thread.run_sync(self.documents.publish_document, identifier)
            if parent:
                activation_started = True
                await anyio.to_thread.run_sync(self.documents.set_document_active, parent.id, False)
                await anyio.to_thread.run_sync(self.documents.set_document_active, identifier, True)
                await self.catalog.activate_version(identifier, parent.id)
            else:
                await self.catalog.update_status(identifier, 'ready')
            return identifier, result.chunks_count
        except BaseException as error:
            if parent and activation_started:
                try:
                    current = await self.catalog.get(identifier)
                except Exception:
                    # Результат фиксации неизвестен. Восстановление при запуске обращается к PostgreSQL
                    # перед изменением видимости поиска любой из версий.
                    logger.exception('Не удалось проверить активацию версии %s', identifier)
                    raise error
                if current and current.is_active and current.status == 'ready':
                    return identifier, current.chunks_count
            # При повторных попытках используются те же ID документа и ID точек.
            try:
                await anyio.to_thread.run_sync(self.documents.delete_document, identifier)
                if parent:
                    await anyio.to_thread.run_sync(self.documents.set_document_active, parent.id, True)
                    await self.catalog.delete(identifier)
            except Exception:
                logger.exception('Не удалось убрать незавершённый индекс %s', identifier)
            raise

    async def index_text(self, request):
        task = BulkUploadTask(uuid.uuid4().hex, request.title + '.txt', request.content.encode('utf-8'),
            request.title, request.owner_id, request.is_public, 'text/plain',
            request.allowed_roles, request.allowed_departments, metadata={**(request.metadata or {}), 'input_format':'manual-text'})
        key = await anyio.to_thread.run_sync(partial(self.store.save, task.data, suffix='.txt'))
        try:
            identifier, chunks = await self(task, key)
            record = await self.catalog.get(identifier)
            if record.storage_key != key:
                await anyio.to_thread.run_sync(self.store.delete, key)
        except BaseException:
            await anyio.to_thread.run_sync(self.store.delete, key)
            raise
        from viio_documents.interfaces._protocols import DocumentMetadata
        return DocumentMetadata(document_id=identifier, title=record.title, chunks_count=chunks, status='indexed')
