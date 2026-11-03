from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.documents import Document

from viio_documents.config import Settings
from viio_rag.app import create_app
from viio_documents.service_profiles import configure_service


READ_KEY = 'r' * 40
PUBLIC_KEY = 'p' * 40


def rag_fixture():
    allowed = Document(page_content='Allowed instruction', metadata={'document_id': 'allowed', 'title': 'Guide'})
    denied = Document(page_content='DENIED_PRIVATE_CANARY', metadata={'document_id': 'denied'})
    vectors = SimpleNamespace(similarity_search=lambda **kwargs: [denied, allowed])
    data = SimpleNamespace(lexical=AsyncMock(return_value=[allowed]), public_index=None,
        authorized_candidates=AsyncMock(return_value=[allowed]), access_revision=AsyncMock(return_value='a' * 32),
        public_search=AsyncMock(return_value=[{'id': 'approved', 'title': 'Public', 'content': 'Approved snapshot'}]),
        public_ids=AsyncMock(return_value=set()))
    settings = Settings(_env_file=None, service_role='rag', secure_runtime=True,
                        documents_read_key=READ_KEY, consultant_source_key=PUBLIC_KEY)
    return create_app(settings, data=data, vector_store=vectors), data


@pytest.mark.asyncio
async def test_rag_private_search_keeps_acl_and_diagnostic_contract():
    app, data = rag_fixture()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://rag') as client:
        payload = {'query': 'instruction', 'diagnostics': True, 'user_context': {'role': 'employee'}}
        assert (await client.post('/api/v1/knowledge/search', json=payload)).status_code == 401
        result = await client.post('/api/v1/knowledge/search', json=payload, headers={'X-Internal-Token': READ_KEY})
        assert result.status_code == 200
        assert result.json()['diagnostics']['selected'][0]['document_id'] == 'allowed'
        assert 'DENIED_PRIVATE_CANARY' not in result.text
        data.authorized_candidates.return_value = []
        result = await client.post('/api/v1/knowledge/search', json=payload, headers={'X-Internal-Token': READ_KEY})
        assert not result.json()['diagnostics']['selected']
        assert 'Allowed instruction' not in result.json()['context']


@pytest.mark.asyncio
async def test_rag_public_and_internal_capabilities_cannot_be_swapped():
    app, data = rag_fixture()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://rag') as client:
        assert (await client.post('/api/v1/knowledge/search', json={'query': 'Guide'}, headers={'X-Internal-Token': PUBLIC_KEY})).status_code == 401
        assert (await client.post('/api/v1/consultant-materials/search', json={'message': 'Guide'}, headers={'X-Publication-Key': READ_KEY})).status_code == 401
        result = await client.post('/api/v1/consultant-materials/search', json={'message': 'Guide'}, headers={'X-Publication-Key': PUBLIC_KEY})
        assert result.status_code == 200 and result.json()['items'][0]['content'] == 'Approved snapshot'
        assert 'Allowed instruction' not in result.text
        assert (await client.post('/api/v1/consultant-materials/search', json={'message': 'Guide', 'user_context': {'role': 'admin'}}, headers={'X-Publication-Key': PUBLIC_KEY})).status_code == 422
        result = await client.post('/api/v1/consultant-materials/validate', json={'publication_ids': []}, headers={'X-Publication-Key': PUBLIC_KEY})
        assert result.json() == {'publication_ids': []}


@pytest.mark.asyncio
async def test_rag_has_revision_and_health_but_no_document_writes():
    app, _ = rag_fixture()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://rag') as client:
        assert (await client.get('/health')).json()['service'] == 'rag'
        assert (await client.get('/api/v1/knowledge/access-revision')).status_code == 401
        assert (await client.get('/api/v1/knowledge/access-revision', headers={'X-Internal-Token': READ_KEY})).json()['revision'] == 'a' * 32
        assert (await client.post('/api/v1/documents', json={})).status_code == 404
        assert (await client.get('/api/v1/documents/download/private')).status_code == 404
        assert (await client.post('/api/v1/chat', json={})).status_code == 404


def gateway_app():
    app = FastAPI()
    app.state.settings = Settings(_env_file=None, service_role='documents',
        rag_url='http://rag', documents_read_key=READ_KEY, consultant_source_key=PUBLIC_KEY,
        documents_admin_key='a' * 40)
    app.state.remote_documents = False
    app.state.catalog_service = SimpleNamespace(available=True)
    app.state.document_service = None
    # A sentinel proves the gateway never invokes local retrieval.
    app.state.knowledge_data = MagicMock()
    configure_service(app)
    return app


@pytest.mark.asyncio
async def test_documents_gateway_forwards_both_scopes_to_rag(monkeypatch):
    rag, _ = rag_fixture()
    gateway = gateway_app()
    real_client = httpx.AsyncClient
    async with real_client(transport=httpx.ASGITransport(gateway), base_url='http://documents') as client:
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.ASGITransport(rag), **kwargs))
        private = await client.post('/api/v1/knowledge/search', json={'query': 'instruction'}, headers={'X-Internal-Token': READ_KEY})
        assert private.status_code == 200 and 'Allowed instruction' in private.json()['context']
        admin = await client.post('/api/v1/knowledge/search', json={'query': 'instruction'}, headers={'X-Internal-Token': 'a' * 40})
        assert admin.status_code == 200 and 'Allowed instruction' in admin.json()['context']
        public = await client.post('/api/v1/consultant-materials/search', json={'message': 'Guide'}, headers={'X-Publication-Key': PUBLIC_KEY})
        assert public.status_code == 200 and public.json()['items'][0]['title'] == 'Public'
        assert (await client.get('/api/v1/knowledge/access-revision', headers={'X-Internal-Token': READ_KEY})).status_code == 200
        gateway.state.knowledge_data.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_failure_does_not_fall_back_to_local_or_public_search(monkeypatch):
    gateway = gateway_app()
    real_client = httpx.AsyncClient
    async def offline(request):
        raise httpx.ConnectError('PRIVATE_NETWORK_DETAILS')
    async with real_client(transport=httpx.ASGITransport(gateway), base_url='http://documents') as client:
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(offline), **kwargs))
        result = await client.post('/api/v1/knowledge/search', json={'query': 'Guide'}, headers={'X-Internal-Token': READ_KEY})
        assert result.status_code == 503 and 'PRIVATE_NETWORK_DETAILS' not in result.text
        gateway.state.knowledge_data.assert_not_called()


@pytest.mark.asyncio
async def test_rag_backend_failure_returns_safe_service_unavailable():
    app, data = rag_fixture()
    data.public_search.side_effect = RuntimeError('PRIVATE_BACKEND_DETAILS')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, raise_app_exceptions=False), base_url='http://rag') as client:
        result = await client.post('/api/v1/consultant-materials/search', json={'message': 'Guide'}, headers={'X-Publication-Key': PUBLIC_KEY})
        assert result.status_code == 503 and 'PRIVATE_BACKEND_DETAILS' not in result.text
