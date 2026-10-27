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
        catalog_service=catalog, file_store=store, bulk_queue=queue, ingestion_service=ingestion, knowledge_data=ingestion.data))
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


async def test_new_version_is_searchable_and_original_stays_downloadable(pipeline):
    client, queue, catalog, adapter, ingestion, _ = pipeline
    root = await ingestion.index_text(DocumentCreateRequest(title='Guide', content='Old version original',
        owner_id='u1', allowed_roles=['admin'], allowed_departments=[]))
    queue.start()
    response = await client.post('/api/v1/documents/bulk-upload',
        params={'version_parent_id': root.document_id, 'is_public': True, 'allowed_roles': 'employee', 'owner_id': 'forged'},
        files=[('files', ('new-guide.txt', b'Current version original', 'text/plain'))])
    assert response.status_code == 202
    result = await terminal(queue, response.json()['tasks'][0]['task_id'])
    assert result.status == 'ready', result.error
    record = await catalog.get(result.document_id)
    assert record.version == 2 and record.parent_id == root.document_id and record.is_active
    assert record.title == 'Guide' and record.owner_id == 'u1' and not record.is_public
    assert record.allowed_roles == ['admin']
    assert await catalog.count() == 1
    assert (await catalog.list_documents()).items[0].id == record.id
    assert {doc.metadata['document_id'] for doc in adapter.similarity_search('version', k=10)} == {record.id}
    history = (await client.get('/api/v1/documents/versions/' + root.document_id)).json()['items']
    assert [item['version'] for item in history] == [2, 1]
    assert [item['is_active'] for item in history] == [True, False]
    for identifier, expected in [(root.document_id, b'Old version original'), (record.id, b'Current version original')]:
        download = await client.get('/api/v1/documents/download/' + identifier, params={'user_role': 'admin'})
        assert download.content == expected
        denied = await client.get('/api/v1/documents/download/' + identifier, params={'user_role': 'employee'})
        assert denied.status_code == 403
    stale = await client.post('/api/v1/documents/bulk-upload', params={'version_parent_id': root.document_id},
        files=[('files', ('stale.txt', b'stale', 'text/plain'))])
    assert stale.status_code == 409
    # Re-uploading an archived file must never overwrite or reactivate it.
    await ingestion.index_text(DocumentCreateRequest(title='Guide', content='Old version original',
        owner_id='u1', allowed_roles=['admin'], allowed_departments=[]))
    assert {doc.metadata['document_id'] for doc in adapter.similarity_search('version', k=10)} == {record.id}


async def test_concurrent_version_upload_rejects_stale_parent(pipeline):
    client, queue, catalog, adapter, ingestion, _ = pipeline
    root = await ingestion.index_text(DocumentCreateRequest(title='Concurrent', content='Initial concurrent version'))
    ids = []
    for content in (b'Second concurrent version', b'Third concurrent version'):
        response = await client.post('/api/v1/documents/bulk-upload', params={'version_parent_id': root.document_id},
            files=[('files', ('version.txt', content, 'text/plain'))])
        ids.append(response.json()['tasks'][0]['task_id'])
    queue.start()
    results = await asyncio.gather(*(terminal(queue, identifier) for identifier in ids))
    assert sorted(result.status for result in results) == ['error', 'ready']
    assert 'версия изменилась' in next(result.error for result in results if result.status == 'error')
    versions = await catalog.versions(root.document_id)
    assert [record.version for record in versions] == [2, 1]
    assert sum(record.is_active for record in versions) == 1


async def test_version_failure_rolls_back_and_retry_does_not_duplicate_history(pipeline):
    _, _, catalog, adapter, ingestion, store = pipeline
    root = await ingestion.index_text(DocumentCreateRequest(title='Rollback', content='Keep previous content'))
    task = BulkUploadTask(uuid.uuid4().hex, 'replacement.txt', b'Replacement content', 'Replacement', None, False,
        'text/plain', [], [], version_parent_id=root.document_id)
    key = store.save(task.data, suffix='.txt')
    activate = catalog.activate_version
    async def failure(*args):
        raise RuntimeError('activation failure')
    catalog.activate_version = failure
    with pytest.raises(RuntimeError, match='activation failure'):
        await ingestion(task, key)
    assert (await catalog.get(root.document_id)).is_active
    assert {doc.page_content for doc in adapter.similarity_search('content', k=10)} == {'Keep previous content'}
    catalog.activate_version = activate
    first = await ingestion(task, key)
    second = await ingestion(task, key)
    assert first == second
    assert [record.version for record in await catalog.versions(root.document_id)] == [2, 1]


async def test_recovery_restores_catalog_authority_for_partial_activation(pipeline):
    _, _, catalog, adapter, ingestion, _ = pipeline
    root = await ingestion.index_text(DocumentCreateRequest(title='Recover', content='Active content'))
    staged = 'doc-' + uuid.uuid4().hex
    ingestion.documents.index_document('Recover', 'Uncommitted content', {'is_active': True, 'ingestion_ready': True}, document_id=staged)
    await catalog.create(doc_id=staged, title='Recover', chunks_count=1, family_id=root.document_id,
        parent_id=root.document_id, version=2, is_active=False, status='processing')
    adapter.set_document_active(root.document_id, False)
    await ingestion.recover_versions()
    assert {doc.page_content for doc in adapter.similarity_search('content', k=10)} == {'Active content'}


async def test_pdf_version_keeps_old_search_until_approval(pipeline):
    from test_pdf_extraction import pdf_bytes
    client, queue, catalog, adapter, ingestion, store = pipeline
    root = await ingestion.index_text(DocumentCreateRequest(title='PDF version', content='Previous approved text'))
    original = pdf_bytes('New PDF extracted text')
    response = await client.post('/api/v1/documents/bulk-upload', params={'version_parent_id': root.document_id},
        files=[('files', ('replacement.pdf', original, 'application/pdf'))])
    identifier = response.json()['tasks'][0]['task_id']
    queue.start()
    assert (await terminal(queue, identifier, ('needs_review', 'error'))).status == 'needs_review'
    assert {doc.page_content for doc in adapter.similarity_search('text', k=10)} == {'Previous approved text'}
    review = (await client.get('/api/v1/documents/bulk-review/' + identifier)).json()
    approval = await client.post('/api/v1/documents/bulk-review/' + identifier, params={'reviewer_id': 'admin'},
        json={'content': 'New approved PDF text', 'review_token': review['token']})
    assert approval.status_code == 200
    result = await terminal(queue, identifier)
    assert result.status == 'ready', result.error
    assert {doc.page_content for doc in adapter.similarity_search('text', k=10)} == {'New approved PDF text'}
    record = await catalog.get(result.document_id)
    assert record.version == 2 and store.load(record.storage_key) == original


async def test_invalid_activation_does_not_remove_current_version(pipeline):
    _, _, catalog, _, ingestion, _ = pipeline
    root = await ingestion.index_text(DocumentCreateRequest(title='Atomic', content='Current atomic text'))
    with pytest.raises(ValueError, match='Подготовленная версия'):
        await catalog.activate_version('doc-missing', root.document_id)
    assert (await catalog.get(root.document_id)).is_active


async def test_code_is_private_even_to_owner_employee_and_has_line_references(pipeline):
    from apiagent.services.enhanced_retrieval import EnhancedRetrieval
    from apiagent.tools.search_tool import build_access_filter, create_knowledge_search_tool
    client, queue, catalog, adapter, ingestion, _ = pipeline
    source = b'import os\n\ndef CalculateInvoiceTotal(items):\n    return sum(items)\n'
    response = await client.post('/api/v1/documents/bulk-upload', params={'owner_id': 'former-admin', 'is_public': True, 'allowed_departments': 'general'},
        files=[('files', ('billing.py', source, 'text/plain'))])
    queue.start()
    result = await terminal(queue, response.json()['tasks'][0]['task_id'])
    assert result.status == 'ready', result.error
    record = await catalog.get(result.document_id)
    assert record.document_kind == 'code' and record.original_filename == 'billing.py'
    assert not record.is_public and record.allowed_departments == []
    employee = {'user_id': 'former-admin', 'role': 'employee', 'departments': ['general']}
    programmer = {'user_id': 'coder', 'role': 'programmer', 'departments': []}
    assert await ingestion.data.lexical('billing', programmer)
    assert not await ingestion.data.lexical('CalculateInvoiceTotal', employee)
    assert not adapter.similarity_search('CalculateInvoiceTotal', filter=build_access_filter(employee))
    denied = await client.get('/api/v1/documents/download/' + record.id, params={'user_role': 'employee', 'user_id': 'former-admin', 'departments': 'general'})
    assert denied.status_code == 403
    tool = EnhancedRetrieval(adapter, ingestion.data).tool(create_knowledge_search_tool(adapter), 3)
    reference = await tool.ainvoke({'query': 'CalculateInvoiceTotal'}, config={'configurable': {'user_context': programmer}})
    assert 'billing.py' in reference and 'строки' in reference and 'CalculateInvoiceTotal' in reference
    original = await client.get('/api/v1/documents/download/' + record.id, params={'user_role': 'programmer'})
    assert original.content == source and 'billing.py' in original.headers['content-disposition']
    publish = await client.post('/api/v1/documents/publication/' + record.id, params={'publisher_id': 'admin'}, json={'title': 'Code', 'content': 'public code'})
    assert publish.status_code == 409


async def test_identical_code_in_different_files_keeps_both_source_names(pipeline):
    client, queue, catalog, _, _, _ = pipeline
    source = b'def shared_helper():\n    return 1\n'
    response = await client.post('/api/v1/documents/bulk-upload', files=[
        ('files', ('first.py', source, 'text/plain')), ('files', ('second.py', source, 'text/plain'))])
    queue.start()
    results = await asyncio.gather(*(terminal(queue, item['task_id']) for item in response.json()['tasks']))
    assert all(item.status == 'ready' for item in results)
    assert len({item.document_id for item in results}) == 2
    assert {item.original_filename for item in (await catalog.list_documents()).items} == {'first.py', 'second.py'}


async def test_lexical_search_filters_departments_and_archived_versions(pipeline):
    from apiagent.services.enhanced_retrieval import EnhancedRetrieval
    _, _, _, adapter, ingestion, _ = pipeline
    await ingestion.index_text(DocumentCreateRequest(title='Policy', content='Согласование служебных поездок',
        allowed_roles=[], allowed_departments=['hr']))
    allowed = {'user_id': 'hr-user', 'role': 'employee', 'departments': ['hr']}
    assert await ingestion.data.lexical('согласования поездок', allowed)
    assert not await ingestion.data.lexical('поездок', {'role': 'employee', 'departments': ['it']})
    # Lexical retrieval remains useful if the semantic service is unavailable.
    class FailedVector:
        def similarity_search(self, **kwargs):
            raise RuntimeError('offline')
    assert await EnhancedRetrieval(FailedVector(), ingestion.data).search('поездок', allowed, 2)


async def test_personal_facts_persist_deduplicate_and_follow_access_scope(pipeline):
    from apiagent.services.personal_memory import PersonalFacts
    _, _, catalog, _, _, _ = pipeline
    facts = PersonalFacts(catalog)
    alice = {'user_id': 'alice', 'role': 'programmer', 'departments': ['it']}
    identifiers = await asyncio.gather(*(facts.remember(alice, 'conversation', 'Use Python 3.12') for _ in range(2)))
    assert identifiers[0] == identifiers[1]
    recovered = PersonalFacts(catalog)
    assert (await recovered.list(alice))[0]['source_thread'] == 'conversation'
    assert not await recovered.list({**alice, 'user_id': 'bob'})
    assert not await recovered.list({**alice, 'role': 'employee'})
    assert not await recovered.list({**alice, 'departments': ['hr']})
    assert not await recovered.forget({**alice, 'user_id': 'bob'}, identifiers[0])
    with pytest.raises(ValueError, match='Пароли'):
        await facts.remember(alice, 'conversation', 'password=secret-value')
    assert await recovered.forget(alice, identifiers[0])
    assert not await recovered.list(alice)


async def test_public_api_never_uses_internal_public_documents_or_client_roles(pipeline):
    from viio_consultant.agent import PublicConsultant
    from langchain_core.messages import AIMessage
    client, _, catalog, _, ingestion, _ = pipeline
    root = await ingestion.index_text(DocumentCreateRequest(title='Internal policy', content='INTERNAL_SECRET internal details', is_public=True))
    class Model:
        def __init__(self):
            self.calls = []
        async def ainvoke(self, messages):
            self.calls.append(messages)
            return AIMessage(content='Support is available Monday.')
    model = Model()
    consultant = PublicConsultant(ingestion.data, model)
    app = FastAPI()
    settings = Settings(consultant_enabled=True, consultant_api_key='k' * 40, internal_api_key='internal')
    app.include_router(create_api_router(MagicMock(), ingestion.documents, settings, catalog_service=catalog,
        knowledge_data=ingestion.data, consultant_service=consultant))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as external:
        path = '/api/v1/consultant/chat'
        assert (await external.post(path, json={'message': 'Support'}, headers={'X-Internal-Token': 'internal'})).status_code == 401
        assert (await external.post(path, json={'message': 'Support', 'user_context': {'role': 'admin'}}, headers={'X-Consultant-Key': 'k' * 40})).status_code == 422
        response = await external.post(path, json={'message': 'Support'}, headers={'X-Consultant-Key': 'k' * 40})
        assert response.status_code == 200 and not response.json()['sources'] and not model.calls
        published = await client.post('/api/v1/documents/publication/' + root.document_id, params={'publisher_id': 'admin'},
            json={'title': 'Public support', 'content': 'Support is available Monday.'})
        assert published.status_code == 200
        response = await external.post(path, json={'message': 'Support'}, headers={'X-Consultant-Key': 'k' * 40})
        assert response.status_code == 200 and len(response.json()['sources']) == 1
        assert 'INTERNAL_SECRET' not in str(model.calls)
        assert set(response.json()['sources'][0]) == {'publication_id', 'title'}
        await client.delete('/api/v1/documents/publication/' + root.document_id)
        response = await external.post(path, json={'message': 'Support'}, headers={'X-Consultant-Key': 'k' * 40})
        assert not response.json()['sources'] and len(model.calls) == 1


async def test_publication_is_withdrawn_on_new_version_and_during_generation(pipeline):
    from viio_consultant.agent import PublicConsultant
    from langchain_core.messages import AIMessage
    _, _, _, _, ingestion, store = pipeline
    root = await ingestion.index_text(DocumentCreateRequest(title='Support', content='Internal support'))
    await ingestion.data.publish(root.document_id, 'Support', 'Support on Monday', 'admin')
    class WithdrawingModel:
        async def ainvoke(self, messages):
            await ingestion.data.withdraw(root.document_id)
            return AIMessage(content='Stale public answer')
    result = await PublicConsultant(ingestion.data, WithdrawingModel()).chat('Support')
    assert not result['sources'] and 'Stale public answer' not in result['reply']
    await ingestion.data.publish(root.document_id, 'Support', 'Support on Monday', 'admin')
    task = BulkUploadTask(uuid.uuid4().hex, 'new.txt', b'New support content', 'Support', None, False,
        'text/plain', [], [], version_parent_id=root.document_id)
    await ingestion(task, store.save(task.data, suffix='.txt'))
    assert not await ingestion.data.public_search('Support')


async def test_standalone_consultant_http_gateway_preserves_publication_boundary(pipeline):
    from viio_consultant import create_app as create_consultant
    from viio_consultant.config import ConsultantSettings
    from viio_consultant.source import HttpPublicationSource
    from viio_consultant.agent import PublicConsultant
    from langchain_core.messages import AIMessage
    _, _, catalog, _, ingestion, _ = pipeline
    root = await ingestion.index_text(DocumentCreateRequest(title='Private', content='PRIVATE_INTERNAL_BODY', is_public=True))
    await ingestion.data.publish(root.document_id, 'Support', 'Support on Monday', 'admin')
    gateway = FastAPI()
    gateway.include_router(create_api_router(MagicMock(), ingestion.documents,
        Settings(internal_api_key='internal', consultant_api_key='a' * 40, consultant_source_key='s' * 40),
        catalog_service=catalog, knowledge_data=ingestion.data))
    class Model:
        async def ainvoke(self, messages):
            assert 'PRIVATE_INTERNAL_BODY' not in str(messages)
            return AIMessage(content='Support on Monday')
    async with httpx.AsyncClient(base_url='http://gateway', transport=httpx.ASGITransport(gateway), headers={'X-Publication-Key': 's' * 40}) as source_client:
        source = HttpPublicationSource(source_client)
        external_app = create_consultant(ConsultantSettings(enabled=True, api_key='a' * 40, source_key='s' * 40),
            agent=PublicConsultant(source, Model()))
        async with httpx.AsyncClient(base_url='http://external', transport=httpx.ASGITransport(external_app), headers={'X-Consultant-Key': 'a' * 40}) as client:
            response = await client.post('/api/v1/consultant/chat', json={'message': 'Support'})
            assert response.status_code == 200 and response.json()['sources']
            await ingestion.data.withdraw(root.document_id)
            response = await client.post('/api/v1/consultant/chat', json={'message': 'Support'})
            assert response.status_code == 200 and not response.json()['sources']
        denied = await source_client.get('/api/v1/documents/catalog')
        assert denied.status_code == 401
        denied = await source_client.post('/api/v1/consultant-materials/search', json={'message': 'Support'},
            headers={'X-Publication-Key': 'a' * 40})
        assert denied.status_code == 401
