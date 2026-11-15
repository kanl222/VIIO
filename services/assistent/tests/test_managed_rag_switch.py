import json
from unittest.mock import AsyncMock
import httpx
import pytest
from assistent import create_app
from assistent.config import Settings


@pytest.mark.asyncio
async def test_cloud_switch_does_not_forward_history_identity_or_call_local_agent(monkeypatch):
    local = AsyncMock()
    settings = Settings(_env_file=None, internal_api_key='test-key', documents_read_key='read-key',
        documents_url='http://documents', assistant_rag_backend='local')
    app = create_app(settings=settings, agent_service=local)
    seen = []
    async def upstream(request):
        seen.append(json.loads(request.content))
        assert request.url.path == '/api/v1/knowledge/yandex/chat'
        assert request.headers['X-Internal-Token'] == 'read-key'
        return httpx.Response(200, json={'reply': 'Public answer', 'sources': [
            {'document_id': 'doc-1', 'title': 'Public guide', 'file_id': 'cloud-file'}]})
    real_client = httpx.AsyncClient
    async with real_client(transport=httpx.ASGITransport(app), base_url='http://agent') as client:
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(
            transport=httpx.MockTransport(upstream), **kwargs))
        payload = {'message': 'Public question', 'thread_id': 'private-thread',
            'user_context': {'user_id': 'private-id', 'role': 'admin', 'departments': ['private']},
            'rag_backend': 'yandex'}
        assert (await client.post('/api/v1/chat', headers={'X-Internal-Token': 'test-key'}, json=payload)).status_code == 403
        assert not seen
        payload['confirmed_public_for_external_cloud'] = True
        result = await client.post('/api/v1/chat', headers={'X-Internal-Token': 'test-key'}, json=payload)
        assert result.status_code == 200
        assert result.json()['thread_id'] == 'private-thread'
        assert seen == [{'message': 'Public question', 'confirmed_public_for_external_cloud': True}]
        local.chat.assert_not_awaited()
