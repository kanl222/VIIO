import json
import httpx
import pytest
from assistent.infrastructure.clients.public_consultant import ConsultantProxy


@pytest.mark.asyncio
async def test_compatibility_proxy_forwards_only_question_and_public_key(monkeypatch):
    original = httpx.AsyncClient
    async def handler(request):
        assert str(request.url) == 'http://consultant:8000/api/v1/consultant/chat'
        assert request.headers['X-Consultant-Key'] == 'public-key'
        assert 'X-Internal-Token' not in request.headers
        assert json.loads(request.content) == {'message': 'Support hours?'}
        return httpx.Response(200, json={'reply': 'Monday', 'sources': []})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))
    assert (await ConsultantProxy('http://consultant:8000', 'public-key').chat('Support hours?'))['reply'] == 'Monday'


@pytest.mark.asyncio
async def test_proxy_preserves_timeout_and_never_falls_back_to_internal_agent(monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(504))))
    with pytest.raises(TimeoutError):
        await ConsultantProxy('http://consultant:8000', 'public-key').chat('Support hours?')
