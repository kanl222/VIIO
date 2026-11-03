import json
from unittest.mock import AsyncMock
import httpx
import pytest
from viio_consultant import create_app
from viio_consultant.config import ConsultantSettings


@pytest.mark.asyncio
async def test_consultant_switch_uses_its_own_trusted_endpoint(monkeypatch):
    local = AsyncMock()
    settings = ConsultantSettings(enabled=True, api_key='a' * 40, source_key='s' * 40,
        source_url='http://documents')
    app = create_app(settings, agent=local)
    seen = []
    async def upstream(request):
        assert request.url.path == '/api/v1/consultant-materials/yandex/chat'
        assert request.headers['X-Publication-Key'] == 's' * 40
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'reply': 'Answer', 'sources': [{'title': 'Guide', 'document_id': 'doc'}]})
    real_client = httpx.AsyncClient
    async with real_client(transport=httpx.ASGITransport(app), base_url='http://consultant') as client:
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(
            transport=httpx.MockTransport(upstream), **kwargs))
        payload = {'message': 'Public question', 'rag_backend': 'yandex'}
        headers = {'X-Consultant-Key': 'a' * 40}
        assert (await client.post('/api/v1/consultant/chat', headers=headers, json=payload)).status_code == 403
        assert not seen
        payload['confirmed_public_for_external_cloud'] = True
        result = await client.post('/api/v1/consultant/chat', headers=headers, json=payload)
        assert result.status_code == 200
        assert seen == [{'message': 'Public question', 'confirmed_public_for_external_cloud': True}]
        local.chat.assert_not_awaited()
