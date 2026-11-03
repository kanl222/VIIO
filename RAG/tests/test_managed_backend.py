import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi import HTTPException
from viio_rag.config import Settings
from viio_rag.schemas import ManagedRagQuestion
from viio_rag.managed import answer


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = tmp_path / 'corpus.json'
    path.write_text(json.dumps({'index_id': 'test-index', 'expires_at':
        (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        'files': [{'file_id': 'file-1', 'document_id': 'doc-1', 'title': 'Guide', 'sha256': 'hash'}]}))
    settings = Settings(_env_file=None, rag_yandex_api_key='test', rag_yandex_folder_id='folder',
        rag_yandex_index_id='test-index', rag_yandex_manifest=str(path))
    response = SimpleNamespace(output_text='Answer from public guide', output=[
        SimpleNamespace(type='file_search_call', status='completed'),
        SimpleNamespace(type='message', content=[SimpleNamespace(annotations=[
            SimpleNamespace(type='file_citation', file_id='file-1')])])])
    create = AsyncMock(return_value=response)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=SimpleNamespace(responses=SimpleNamespace(create=create)))
    client.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=client)
    monkeypatch.setattr('viio_rag.managed.AsyncOpenAI', factory)
    data = SimpleNamespace(cloud_corpus_is_public=AsyncMock(return_value=True))
    return settings, data, create, factory


@pytest.mark.asyncio
async def test_roles_use_separate_prompts_and_only_current_public_question(setup):
    settings, data, create, _ = setup
    question = ManagedRagQuestion(message='Public question', confirmed_public_for_external_cloud=True)
    for role, prompt in [('assistant', settings.rag_yandex_assistant_prompt),
                         ('consultant', settings.rag_yandex_consultant_prompt)]:
        result = await answer(settings, data, question, role)
        kwargs = create.call_args.kwargs
        assert kwargs['instructions'] == prompt
        assert kwargs['input'] == 'Public question'
        assert kwargs['store'] is False
        assert 'previous_response_id' not in kwargs
        assert result['sources'][0]['document_id'] == 'doc-1'
    assert settings.rag_yandex_assistant_prompt != settings.rag_yandex_consultant_prompt


@pytest.mark.asyncio
async def test_confirmation_blocks_before_any_cloud_access(setup):
    settings, data, _, factory = setup
    with pytest.raises(HTTPException) as error:
        await answer(settings, data, ManagedRagQuestion(message='Question'), 'assistant')
    assert error.value.status_code == 403
    factory.assert_not_called()
    data.cloud_corpus_is_public.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_corpus_blocks_before_cloud_access(setup):
    settings, data, _, factory = setup
    data.cloud_corpus_is_public.return_value = False
    with pytest.raises(HTTPException) as error:
        await answer(settings, data, ManagedRagQuestion(message='Question',
            confirmed_public_for_external_cloud=True), 'consultant')
    assert error.value.status_code == 503
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_unverified_answer_is_not_returned(setup):
    settings, data, create, _ = setup
    create.return_value = SimpleNamespace(output_text='Unsupported claim', output=[])
    with pytest.raises(HTTPException) as error:
        await answer(settings, data, ManagedRagQuestion(message='Question',
            confirmed_public_for_external_cloud=True), 'assistant')
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_revocation_during_generation_blocks_answer(setup):
    settings, data, _, _ = setup
    data.cloud_corpus_is_public.side_effect = [True, False]
    with pytest.raises(HTTPException) as error:
        await answer(settings, data, ManagedRagQuestion(message='Question',
            confirmed_public_for_external_cloud=True), 'assistant')
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_real_routes_fix_role_and_require_different_service_capabilities(monkeypatch):
    import httpx
    from viio_rag.app import create_app
    settings = Settings(_env_file=None, documents_read_key='r' * 40, consultant_source_key='p' * 40)
    responder = AsyncMock(return_value={'reply': 'Answer', 'sources': []})
    monkeypatch.setattr('viio_rag.managed.answer', responder)
    app = create_app(settings, data=SimpleNamespace(), vector_store=SimpleNamespace())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://rag') as client:
        body = {'message': 'Public question', 'confirmed_public_for_external_cloud': True}
        assistant = '/api/v1/knowledge/yandex/chat'
        consultant = '/api/v1/consultant-materials/yandex/chat'
        assert (await client.post(assistant, json=body)).status_code == 401
        assert (await client.post(consultant, headers={'X-Publication-Key': 'r' * 40}, json=body)).status_code == 401
        assert (await client.post(assistant, headers={'X-Internal-Token': 'r' * 40}, json=body)).status_code == 200
        assert responder.call_args.args[3] == 'assistant'
        assert (await client.post(consultant, headers={'X-Publication-Key': 'p' * 40}, json=body)).status_code == 200
        assert responder.call_args.args[3] == 'consultant'
        assert (await client.post(consultant, headers={'X-Publication-Key': 'p' * 40},
            json={**body, 'role': 'assistant'})).status_code == 422
