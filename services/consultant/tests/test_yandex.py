import httpx
import pytest
from fastapi.testclient import TestClient
from viio_consultant import create_app
from viio_consultant.config import ConsultantSettings


def test_public_confirmation_and_auth_fail_before_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Network must not be used')
    monkeypatch.setattr(httpx.AsyncClient, 'post', forbidden)
    settings = ConsultantSettings(yandex_enabled=True, api_key='a' * 40,
        yandex_api_key='test', yandex_folder_id='folder')
    with TestClient(create_app(settings)) as client:
        url = '/api/v1/consultant/yandex/chat'
        headers = {'X-Consultant-Key': 'a' * 40}
        assert client.post(url, json={'message': 'Public', 'confirmed_public_for_external_cloud': True}).status_code == 401
        assert client.post(url, headers=headers, json={'message': 'Public'}).status_code == 422
        assert client.post(url, headers=headers, json={'message': 'Public', 'confirmed_public_for_external_cloud': False}).status_code == 403
        assert client.post(url, headers=headers, json={'message': 'Public', 'confirmed_public_for_external_cloud': 'true'}).status_code == 422
        assert client.post(url, headers=headers, json={'message': 'Public', 'confirmed_public_for_external_cloud': True, 'documents': []}).status_code == 422


def test_only_confirmed_text_is_sent(monkeypatch):
    async def post(self, url, **kwargs):
        assert url == 'https://ai.api.cloud.yandex.net/v1/chat/completions'
        assert kwargs['json']['messages'] == [{'role': 'user', 'content': 'Public text'}]
        assert kwargs['json']['model'] == 'gpt://folder/aliceai-llm'
        assert 'tools' not in kwargs['json']
        return httpx.Response(200, request=httpx.Request('POST', url),
            json={'choices': [{'message': {'content': 'Answer'}}]})
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    settings = ConsultantSettings(yandex_enabled=True, api_key='a' * 40,
        yandex_api_key='test', yandex_folder_id='folder')
    with TestClient(create_app(settings)) as client:
        response = client.post('/api/v1/consultant/yandex/chat', headers={'X-Consultant-Key': 'a' * 40},
            json={'message': 'Public text', 'confirmed_public_for_external_cloud': True})
        assert response.json() == {'reply': 'Answer', 'sources': []}


def test_yandex_requires_credentials():
    with pytest.raises(ValueError):
        ConsultantSettings(yandex_enabled=True)


def test_knowledge_consultant_requires_question_confirmation():
    from unittest.mock import AsyncMock
    agent = AsyncMock()
    settings = ConsultantSettings(enabled=True, provider='yandex', api_key='a' * 40,
        source_key='s' * 40, yandex_api_key='test', yandex_folder_id='folder')
    with TestClient(create_app(settings, agent=agent)) as client:
        response = client.post('/api/v1/consultant/chat', headers={'X-Consultant-Key': 'a' * 40},
            json={'message': 'Public text'})
        assert response.status_code == 403
        agent.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloud_materials_require_exact_content_approval():
    import hashlib
    from viio_consultant.infrastructure.source import HttpPublicationSource
    public = {'id': 'approved', 'title': 'Title', 'content': 'Public content'}
    changed = {**public, 'id': 'changed', 'content': 'Changed content'}
    digest = hashlib.sha256(b'Title\nPublic content').hexdigest()
    async with httpx.AsyncClient(base_url='http://gateway', transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={'items': [public, changed]}))) as client:
        assert await HttpPublicationSource(client, cloud_public_material_hashes=[]).public_search('query') == []
        assert await HttpPublicationSource(client, cloud_public_material_hashes=[digest]).public_search('query') == [public]


def test_yandex_model_factory():
    from unittest.mock import patch
    from viio_consultant.infrastructure.models import create_model
    settings = ConsultantSettings(provider='yandex', api_key='a' * 40,
        yandex_api_key='test', yandex_folder_id='folder')
    with patch('viio_consultant.infrastructure.models.ChatOpenAI') as model:
        create_model(settings)
        assert model.call_args.kwargs['model'] == 'gpt://folder/aliceai-llm'
        assert model.call_args.kwargs['base_url'] == 'https://ai.api.cloud.yandex.net/v1'

        # Local model name is ignored and safely falls back to default yandex model
        settings_with_local = ConsultantSettings(provider='yandex', api_key='a' * 40,
            yandex_api_key='test', yandex_folder_id='folder', model='qwen/qwen3-vl-8b')
        create_model(settings_with_local)
        assert model.call_args.kwargs['model'] == 'gpt://folder/aliceai-llm'

        # Valid yandex model name is respected
        settings_with_yandex = ConsultantSettings(provider='yandex', api_key='a' * 40,
            yandex_api_key='test', yandex_folder_id='folder', model='yandexgpt/latest')
        create_model(settings_with_yandex)
        assert model.call_args.kwargs['model'] == 'gpt://folder/yandexgpt/latest'


@pytest.mark.asyncio
async def test_cloud_accepts_only_explicit_public_access_class():
    from viio_consultant.infrastructure.source import HttpPublicationSource
    base = {'id': 'doc', 'title': 'Title', 'content': 'Public text'}
    rows = [base, {**base, 'id': 'restricted', 'access_class': 'restricted'},
        {**base, 'id': 'public', 'access_class': 'public'}]
    async with httpx.AsyncClient(base_url='http://gateway', transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={'items': rows}))) as client:
        items = await HttpPublicationSource(client, cloud_public_only=True).public_search('query')
        assert items == [{**base, 'id': 'public'}]
