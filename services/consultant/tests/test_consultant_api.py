import ast
from pathlib import Path
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi.testclient import TestClient
from viio_consultant import create_app
from viio_consultant.config import ConsultantSettings
from viio_consultant.source import HttpPublicationSource
from viio_consultant.agent import clean_reply


def settings():
    return ConsultantSettings(enabled=True, api_key='a' * 40, source_key='s' * 40)


def test_standalone_routes_never_expose_internal_tools_or_document_management():
    agent = AsyncMock()
    agent.chat.return_value = {'reply': 'Published reply', 'sources': []}
    with TestClient(create_app(settings(), agent=agent)) as client:
        assert client.get('/health').status_code == 200
        assert client.post('/api/v1/consultant/chat', json={'message': 'Help'}).status_code == 401
        assert client.post('/api/v1/consultant/chat', json={'message': 'Help'}, headers={'X-Consultant-Key': 's' * 40}).status_code == 401
        assert client.post('/api/v1/consultant/chat', json={'message': 'Help', 'user_context': {'role': 'admin'}}, headers={'X-Consultant-Key': 'a' * 40}).status_code == 422
        result = client.post('/api/v1/consultant/chat', json={'message': 'Help'}, headers={'X-Consultant-Key': 'a' * 40})
        assert result.status_code == 200 and result.json()['reply'] == 'Published reply'
        for route in ('/api/v1/chat', '/api/v1/documents/catalog', '/api/v1/documents/bulk-upload', '/api/v1/consultant-materials/search'):
            assert client.post(route, json={}).status_code == 404
    agent.chat.assert_awaited_once_with('Help')


def test_disabled_service_starts_without_secrets_or_model():
    with TestClient(create_app(ConsultantSettings(enabled=False))) as client:
        assert client.get('/health').json()['enabled'] is False
        assert client.post('/api/v1/consultant/chat', json={'message': 'Help'}).status_code == 503


def test_enabled_service_requires_distinct_keys():
    with pytest.raises(ValueError):
        ConsultantSettings(enabled=True, api_key='short', source_key='short')
    with pytest.raises(ValueError):
        ConsultantSettings(enabled=True, api_key='a' * 40, source_key='a' * 40)


def test_model_source_markup_is_replaced_with_published_title():
    docs = [{'title': 'Support & hours'}]
    result = clean_reply('Open weekdays. Source: <material title="Support &amp; hours">\n</material>', docs)
    assert result == 'Open weekdays. Source: Support & hours'
    assert 'Invented private source' not in clean_reply('<material title="Invented private source">', docs)
    assert clean_reply([{'type': 'text', 'text': 'Readable answer'}], docs) == 'Readable answer'


@pytest.mark.asyncio
async def test_source_uses_only_published_gateway_and_fails_closed():
    seen = []
    async def handler(request):
        seen.append(request.url.path)
        assert request.headers['X-Publication-Key'] == 's' * 40
        if request.url.path.endswith('/search'):
            return httpx.Response(200, json={'items': [{'id': 'public', 'title': 'Support', 'content': 'Hours'}]})
        return httpx.Response(200, json={'publication_ids': ['public']})
    async with httpx.AsyncClient(base_url='http://gateway', headers={'X-Publication-Key': 's' * 40}, transport=httpx.MockTransport(handler)) as client:
        source = HttpPublicationSource(client)
        assert (await source.public_search('Hours'))[0]['title'] == 'Support'
        assert await source.public_ids(['public']) == {'public'}
    assert seen == ['/api/v1/consultant-materials/search', '/api/v1/consultant-materials/validate']
    async with httpx.AsyncClient(base_url='http://gateway', transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await HttpPublicationSource(client).public_search('Hours')


@pytest.mark.asyncio
async def test_source_rejects_private_fields_from_gateway():
    payload = {'items': [{'id': 'public', 'title': 'Title', 'content': 'Text', 'internal_path': '/private'}]}
    async with httpx.AsyncClient(base_url='http://gateway', transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        with pytest.raises(ValueError):
            await HttpPublicationSource(client).public_search('Title')


def test_standalone_package_does_not_import_internal_application_or_databases():
    forbidden = {'assistent', 'psycopg', 'qdrant_client', 'mcp'}
    root = Path(__file__).resolve().parents[1] / 'src' / 'viio_consultant'
    assert root.is_dir()
    for source in root.glob('*.py'):
        tree = ast.parse(source.read_text(encoding='utf-8'))
        imported = {alias.name.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported.update(node.module.split('.')[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module)
        assert not imported.intersection(forbidden), source
