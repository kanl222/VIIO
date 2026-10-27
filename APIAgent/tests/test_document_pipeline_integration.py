"""Opt-in real PostgreSQL checks; each run owns and removes an isolated schema.

VIIO_TEST_PG_DSN must point to a disposable/development PostgreSQL database.
Vectors and original files are isolated in memory and tmp_path respectively.
"""

import asyncio
import os
import uuid
from unittest.mock import MagicMock

import httpx
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
import pytest
import pytest_asyncio
from fastapi import FastAPI
from langchain_core.embeddings import DeterministicFakeEmbedding
from qdrant_client import QdrantClient

from apiagent.config import Settings
from apiagent.router import create_api_router, DocumentCreateRequest
from apiagent.services.bulk_queue import BulkUploadTask
from apiagent.services.document_catalog import DocumentCatalogService
from apiagent.services.document_ingestion import DocumentIngestion
from apiagent.services.document_service import DocumentService
from apiagent.services.file_store import LocalFileStore
from apiagent.services.persistent_bulk_queue import PersistentBulkQueue
from apiagent.utils.vector_store import QdrantVectorStoreAdapter

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    not os.getenv('VIIO_TEST_PG_DSN'), reason='Set VIIO_TEST_PG_DSN to run isolated PostgreSQL integration tests')]


@pytest_asyncio.fixture
async def pipeline(tmp_path):
    dsn = os.environ['VIIO_TEST_PG_DSN']
    schema = 'viio_test_' + uuid.uuid4().hex
    connection = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    await connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    pool = AsyncConnectionPool(dsn, min_size=1, max_size=8, open=False,
        kwargs={'row_factory': dict_row, 'options': f'-c search_path={schema}'})
    await pool.open(wait=True)
    vectors = QdrantClient(':memory:')
    adapter = QdrantVectorStoreAdapter(vectors, DeterministicFakeEmbedding(size=8), 'test_documents')
    documents = DocumentService(adapter, chunk_size=100, chunk_overlap=10)
    catalog = DocumentCatalogService(pool)
    await catalog.setup()
    store = LocalFileStore(tmp_path)
    settings = Settings(internal_api_key='integration-key')
    ingestion = DocumentIngestion(documents, catalog, store, settings)
    queue = PersistentBulkQueue(ingestion, catalog, store, max_workers=2)
    await queue.setup()
    app = FastAPI()
    app.include_router(create_api_router(MagicMock(), documents, settings,
        catalog_service=catalog, file_store=store, bulk_queue=queue, ingestion_service=ingestion))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test',
                                headers={'X-Internal-Token': 'integration-key'}) as client:
        try:
            yield client, queue, catalog, adapter, ingestion, store
        finally:
            await queue.stop()
            await pool.close()
            vectors.close()
            # Only the randomly named schema created by this fixture is removed.
            await connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
            await connection.close()


async def terminal(queue, identifier, states=('ready', 'error')):
    async with asyncio.timeout(15):
        while True:
            statuses = await queue.get_statuses([identifier])
            if statuses and statuses[0].status in states:
                return statuses[0]
            await asyncio.sleep(0.05)


async def test_upload_survives_new_worker_and_download_checks_full_acl(pipeline):
    client, queue, catalog, adapter, ingestion, store = pipeline
    original = 'Правило отдела кадров: отпуск согласуется заранее.'.encode()
    response = await client.post('/api/v1/documents/bulk-upload',
        params={'allowed_roles': '', 'allowed_departments': 'hr', 'owner_id': 'owner'},
        files=[('files', ('Правила.txt', original, 'text/plain'))])
    assert response.status_code == 202
    identifier = response.json()['tasks'][0]['task_id']
    # Recreate the worker object: queued state and bytes come from durable storage.
    recovered = PersistentBulkQueue(ingestion, catalog, store)
    recovered.start()
    try:
        result = await terminal(recovered, identifier)
        assert result.status == 'ready', result.error
    finally:
        await recovered.stop()
    assert await catalog.count() == 1
    listing = await catalog.list_documents(status='ready', owner_id='owner')
    assert listing.total == 1
    record = listing.items[0]
    assert store.load(record.storage_key) == original
    for context, expected in [({'user_role': 'employee'}, 403),
        ({'user_role': 'employee', 'departments': 'hr'}, 200),
        ({'user_role': 'employee', 'user_id': 'owner'}, 200), ({'user_role': 'admin'}, 200)]:
        download = await client.get(f'/api/v1/documents/download/{record.id}', params=context)
        assert download.status_code == expected
        if expected == 200:
            assert download.content == original
            assert "filename*=UTF-8''" in download.headers['content-disposition']
    assert adapter.similarity_search('Правило', k=2)


async def test_concurrent_dedup_respects_access_and_metadata(pipeline):
    _, _, catalog, adapter, ingestion, _ = pipeline
    request = DocumentCreateRequest(title='Instructions', content='same content',
        owner_id='u1', allowed_roles=['employee'], allowed_departments=[], metadata={'source': 'manual'})
    first, second = await asyncio.gather(ingestion.index_text(request), ingestion.index_text(request))
    assert first.document_id == second.document_id
    assert await catalog.count() == 1
    assert adapter.similarity_search('same', k=1)[0].metadata['source'] == 'manual'
    await ingestion.index_text(request.model_copy(update={'allowed_roles': ['admin']}))
    assert await catalog.count() == 2


async def test_failed_job_can_retry_and_expired_claim_recovers(pipeline):
    client, queue, catalog, adapter, ingestion, store = pipeline
    task = BulkUploadTask(uuid.uuid4().hex, 'retry.txt', b'retry content', 'Retry', 'u1', False,
                          'text/plain', ['employee'], [])
    await queue.enqueue(task)
    claimed = await queue.jobs.claim()
    assert claimed['id'] == task.task_id
    async with catalog.pool.connection() as conn:
        await conn.execute("UPDATE document_upload_jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (task.task_id,))
    async def failing_processor(task, key):
        raise RuntimeError('simulated index failure')
    failed = PersistentBulkQueue(failing_processor, catalog, store)
    failed.start()
    try:
        assert (await terminal(failed, task.task_id)).status == 'error'
    finally:
        await failed.stop()
    response = await client.post(f'/api/v1/documents/bulk-retry/{task.task_id}')
    assert response.status_code == 200
    queue.start()
    assert (await terminal(queue, task.task_id)).status == 'ready'
    assert await catalog.count() == 1
    assert adapter.count() == 1


async def test_partial_index_is_hidden_and_failure_is_not_ready(pipeline):
    _, _, catalog, adapter, ingestion, _ = pipeline
    ingestion.documents.index_document('Hidden', 'staged secret', {'ingestion_ready': False}, document_id='doc-hidden')
    assert not adapter.similarity_search('secret', k=2)
    adapter.publish_document('doc-hidden')
    assert adapter.similarity_search('secret', k=2)
    original_create = catalog.create
    async def unavailable(**kwargs):
        raise RuntimeError('catalog unavailable')
    catalog.create = unavailable
    with pytest.raises(RuntimeError, match='catalog unavailable'):
        await ingestion.index_text(DocumentCreateRequest(title='Fail', content='must not be visible'))
    catalog.create = original_create
    assert adapter.count() == 1
    assert await catalog.count() == 0


async def test_fifty_files_keep_independent_results(pipeline):
    client, queue, catalog, _, _, _ = pipeline
    files = [('files', (f'file-{index}.txt' if index % 2 else f'file-{index}.md',
              f'Unique document number {index}'.encode(), 'text/plain')) for index in range(49)]
    files.append(('files', ('broken.pdf', b'%PDF-invalid', 'application/pdf')))
    response = await client.post('/api/v1/documents/bulk-upload', files=files)
    assert response.status_code == 202
    tasks = response.json()['tasks']
    assert len(tasks) == 50
    queue.start()
    results = await asyncio.gather(*(terminal(queue, task['task_id']) for task in tasks))
    assert sum(result.status == 'ready' for result in results) == 49
    assert sum(result.status == 'error' for result in results) == 1
    assert await catalog.count() == 49


async def test_shutdown_requeues_inflight_job(pipeline):
    _, _, catalog, _, ingestion, store = pipeline
    started = asyncio.Event()
    async def paused(task, key):
        started.set()
        await asyncio.Event().wait()
    first = PersistentBulkQueue(paused, catalog, store)
    identifier = uuid.uuid4().hex
    await first.enqueue(BulkUploadTask(identifier, 'shutdown.txt', b'shutdown content', 'Shutdown',
        'u1', False, 'text/plain', ['employee'], []))
    first.start()
    async with asyncio.timeout(5):
        await started.wait()
    await first.stop()
    assert (await first.get_statuses([identifier]))[0].status == 'queued'
    second = PersistentBulkQueue(ingestion, catalog, store)
    second.start()
    try:
        assert (await terminal(second, identifier)).status == 'ready'
    finally:
        await second.stop()


async def test_pdf_waits_for_review_and_preserves_approved_text_after_restart(pipeline):
    from test_pdf_extraction import pdf_bytes
    client, queue, catalog, adapter, ingestion, store = pipeline
    original = pdf_bytes('Uncorrected extracted text.')
    response = await client.post('/api/v1/documents/bulk-upload', files=[('files', ('review.pdf', original, 'application/pdf'))])
    identifier = response.json()['tasks'][0]['task_id']
    queue.start()
    assert (await terminal(queue, identifier, ('needs_review', 'error'))).status == 'needs_review'
    assert adapter.count() == 0 and await catalog.count() == 0
    await queue.stop()
    # Review and approval survive recreating the queue; raw bytes stay unchanged.
    review = (await client.get(f'/api/v1/documents/bulk-review/{identifier}')).json()
    assert 'Uncorrected' in review['content']
    statuses = (await client.get('/api/v1/documents/bulk-status')).json()
    assert 'review' not in statuses['tasks'][0] and 'content' not in statuses['tasks'][0]
    approval = {'review_token': review['token'], 'content': 'Corrected and approved by administrator.'}
    wrong = await client.post(f'/api/v1/documents/bulk-review/{identifier}?reviewer_id=admin', json={**approval, 'review_token': '0' * 32})
    assert wrong.status_code == 409
    assert (await client.post(f'/api/v1/documents/bulk-review/{identifier}?reviewer_id=admin', json=approval)).status_code == 200
    assert (await client.post(f'/api/v1/documents/bulk-review/{identifier}?reviewer_id=admin', json=approval)).status_code == 409
    recovered = PersistentBulkQueue(ingestion, catalog, store)
    recovered.start()
    try:
        result = await terminal(recovered, identifier)
        assert result.status == 'ready', result.error
    finally:
        await recovered.stop()
    record = await catalog.get(result.document_id)
    assert store.load(record.storage_key) == original
    content = adapter.similarity_search('Corrected', k=2)[0]
    assert content.page_content == approval['content']
    assert content.metadata['reviewed_by'] == 'admin'
    assert content.metadata['extraction_engine'] == 'pypdf'


async def test_cancel_is_atomic_and_processing_cannot_be_cancelled(pipeline):
    client, queue, catalog, adapter, _, _ = pipeline
    response = await client.post('/api/v1/documents/bulk-upload', files=[('files', ('cancel.txt', b'cancel me', 'text/plain'))])
    identifier = response.json()['tasks'][0]['task_id']
    assert (await client.post(f'/api/v1/documents/bulk-cancel/{identifier}')).status_code == 200
    assert await queue.jobs.claim() is None
    assert (await queue.get_statuses([identifier]))[0].status == 'cancelled'
    assert (await client.post(f'/api/v1/documents/bulk-retry/{identifier}')).status_code == 409
    response = await client.post('/api/v1/documents/bulk-upload', files=[('files', ('processing.txt', b'processing', 'text/plain'))])
    identifier = response.json()['tasks'][0]['task_id']
    claim = await queue.jobs.claim()
    assert claim['id'] == identifier
    assert (await client.post(f'/api/v1/documents/bulk-cancel/{identifier}')).status_code == 409
    assert adapter.count() == 0 and await catalog.count() == 0


async def test_cancelled_review_cannot_be_approved(pipeline):
    from test_pdf_extraction import pdf_bytes
    client, queue, _, adapter, _, _ = pipeline
    response = await client.post('/api/v1/documents/bulk-upload', files=[('files', ('cancel.pdf', pdf_bytes(), 'application/pdf'))])
    identifier = response.json()['tasks'][0]['task_id']
    queue.start()
    assert (await terminal(queue, identifier, ('needs_review', 'error'))).status == 'needs_review'
    review = (await client.get(f'/api/v1/documents/bulk-review/{identifier}')).json()
    assert (await client.post(f'/api/v1/documents/bulk-cancel/{identifier}')).status_code == 200
    response = await client.post(f'/api/v1/documents/bulk-review/{identifier}?reviewer_id=admin',
        json={'content': review['content'], 'review_token': review['token']})
    assert response.status_code == 409
    assert adapter.count() == 0


async def test_existing_upload_schema_migrates_without_losing_jobs(pipeline):
    _, queue, catalog, _, _, _ = pipeline
    task = BulkUploadTask(uuid.uuid4().hex, 'legacy.txt', b'legacy queued', 'Legacy', 'u1', False, 'text/plain', [], [])
    await queue.enqueue(task)
    async with catalog.pool.connection() as conn:
        await conn.execute('ALTER TABLE document_upload_jobs DROP CONSTRAINT document_upload_jobs_status_check')
        await conn.execute("ALTER TABLE document_upload_jobs ADD CONSTRAINT document_upload_jobs_status_check CHECK(status IN ('queued','processing','ready','error'))")
        await conn.execute('ALTER TABLE document_upload_jobs DROP COLUMN review')
    await queue.setup()
    assert (await queue.get_statuses([task.task_id]))[0].status == 'queued'
    assert await queue.cancel(task.task_id)
