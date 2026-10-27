import json
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from assistent import create_app
from assistent.config import Settings
from assistent.services.documents_client import create_remote_search_tool
from assistent.service_profiles import configure_service


def test_assistent_never_constructs_document_processing_or_embedding_components(tmp_path):
    settings = Settings(documents_url='http://documents:8000', documents_storage_path=str(tmp_path/'must-not-exist'))
    with patch('assistent.QdrantClient', side_effect=AssertionError('local Qdrant')), \
         patch('assistent.create_embeddings', side_effect=AssertionError('local embeddings')), \
         patch('assistent.DocumentIngestion', side_effect=AssertionError('local ingestion')):
        app = create_app(settings, agent_service=MagicMock())
    assert app.state.remote_documents
    assert app.state.ingestion_service is None and app.state.bulk_queue is None
    assert app.state.vector_store is None and app.state.knowledge_data is None
    assert not (tmp_path/'must-not-exist').exists()
    assert '/api/v1/consultant-materials/search' not in app.openapi()['paths']


def test_empty_document_url_never_enables_embedded_processing():
    with pytest.raises(ValueError,match='DOCUMENTS_URL'):
        create_app(Settings(documents_url=''),agent_service=MagicMock())


@pytest.mark.parametrize('role', ['documents','document-worker'])
def test_document_processes_never_create_chat_or_coder_models(role, tmp_path):
    with patch('assistent.create_chat_model', side_effect=AssertionError('chat model')), \
         patch('assistent.create_coding_model', side_effect=AssertionError('coder')), \
         patch('assistent.QdrantClient'), patch('assistent.create_embeddings'), \
         patch('assistent.QdrantVectorStoreAdapter'):
        app = create_app(Settings(service_role=role, documents_storage_path=str(tmp_path)))
    paths=app.openapi()['paths']
    assert '/api/v1/chat' not in paths and '/api/v1/consultant/chat' not in paths
    assert app.state.bulk_queue is not None
    assert not app.state.enable_postgres_checkpointer
    assert ('/api/v1/knowledge/search' in paths) == (role=='documents')


@pytest.mark.asyncio
@pytest.mark.parametrize('context', [None, {'user_id':'employee','role':'employee','departments':['hr'],'_user_notes':'PRIVATE_NOTE'}])
async def test_remote_search_sends_only_trusted_scope_and_no_personal_notes(monkeypatch,context):
    original=httpx.AsyncClient
    async def handle(request):
        assert str(request.url)=='http://documents:8000/api/v1/knowledge/search'
        assert request.headers['X-Internal-Token']=='service-secret'
        payload=json.loads(request.content)
        assert payload['k']==3 and payload['query']=='правила'
        assert 'PRIVATE_NOTE' not in request.content.decode()
        assert payload['user_context']==({k:context[k] for k in ['user_id','role','departments']} if context else None)
        return httpx.Response(200,json={'context':'<context source="Guide">Allowed text</context>'})
    monkeypatch.setattr('assistent.services.documents_client.httpx.AsyncClient',
        lambda **kwargs: original(**kwargs,transport=httpx.MockTransport(handle)))
    tool=create_remote_search_tool(Settings(documents_url='http://documents:8000',internal_api_key='service-secret'),3)
    result=await tool.ainvoke({'query':'правила'},config={'configurable':{'user_context':context}})
    assert 'Allowed text' in result


@pytest.mark.asyncio
@pytest.mark.parametrize('path',['/api/v1/documents','/api/v1/documents/extract-office'])
async def test_document_api_forwards_heavy_work_but_retains_control_routes(monkeypatch,path):
    app=FastAPI()
    app.state.settings=Settings(service_role='documents',internal_api_key='service-secret')
    app.state.remote_documents=False
    app.state.catalog_service=MagicMock(available=True)
    app.state.vector_store=MagicMock()
    app.state.knowledge_data=MagicMock()
    @app.post('/api/v1/documents')
    @app.post('/api/v1/documents/extract-office')
    async def bad_local_index():
        raise AssertionError('API must not index locally')
    @app.get('/api/v1/documents/catalog')
    async def catalog():
        return {'items':[]}
    configure_service(app)
    from fastapi.responses import JSONResponse
    async def forward(request, url, settings):
        from assistent.services.documents_client import require_internal
        require_internal(request,settings)
        assert url=='http://document-worker:8000'
        return JSONResponse({'document_id':'worker-owned'},status_code=201)
    monkeypatch.setattr('assistent.service_profiles.forward_documents',forward)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://documents') as client:
        assert (await client.post(path,json={})).status_code==401
        result=await client.post(path,json={},headers={'X-Internal-Token':'service-secret'})
        assert result.status_code==201 and result.json()['document_id']=='worker-owned'
        assert (await client.get('/api/v1/documents/catalog')).status_code==200


@pytest.mark.asyncio
async def test_remote_search_fails_closed_without_local_fallback(monkeypatch):
    original=httpx.AsyncClient
    monkeypatch.setattr('assistent.services.documents_client.httpx.AsyncClient',lambda **kwargs: original(
        **kwargs,transport=httpx.MockTransport(lambda _:httpx.Response(503))))
    result=await create_remote_search_tool(Settings()).ainvoke({'query':'секрет'})
    assert 'Ошибка' in result and 'секрет' not in result


@pytest.mark.asyncio
async def test_bulk_processing_waits_for_shared_worker_slot():
    from assistent.services.persistent_bulk_queue import PersistentBulkQueue
    slots=asyncio.Semaphore(1)
    queue=PersistentBulkQueue(MagicMock(),MagicMock(),MagicMock(),processing_slots=slots)
    queue._process_unlimited=AsyncMock(return_value=('document',1))
    async with slots:
        task=asyncio.create_task(queue._process({'id':'queued'}))
        await asyncio.sleep(0)
        queue._process_unlimited.assert_not_awaited()
    assert await task==('document',1)


@pytest.mark.asyncio
async def test_worker_busy_with_indexing_does_not_block_its_health_or_other_services():
    app=FastAPI()
    app.state.settings=Settings(service_role='document-worker',internal_api_key='service-secret')
    app.state.remote_documents=False
    app.state.catalog_service=MagicMock(available=True)
    app.state.processing_slots=asyncio.Semaphore(1)
    @app.post('/api/v1/documents')
    async def index():
        return {'status':'indexed'}
    configure_service(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://worker') as client:
        async with app.state.processing_slots:
            task=asyncio.create_task(client.post('/api/v1/documents',headers={'X-Internal-Token':'service-secret'}))
            await asyncio.sleep(0)
            async with asyncio.timeout(1):
                assert (await client.get('/health')).status_code==200
            assert not task.done()
        assert (await task).status_code==200


@pytest.mark.asyncio
async def test_document_count_is_owned_by_document_api_not_agent_or_worker():
    for role in ['documents','document-worker','assistent']:
        app=FastAPI()
        app.state.settings=Settings(service_role=role)
        app.state.remote_documents=role=='assistent'
        app.state.catalog_service=MagicMock(available=True)
        app.state.document_service=MagicMock()
        app.state.document_service.count_documents.return_value=782
        app.state.vector_store=MagicMock()
        app.state.knowledge_data=MagicMock()
        app.state.processing_slots=asyncio.Semaphore(1)
        configure_service(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://service') as client:
            body=(await client.get('/health')).json()
        assert body.get('documents_count')==(782 if role=='documents' else None)
        assert app.state.document_service.count_documents.call_count==(1 if role=='documents' else 0)
