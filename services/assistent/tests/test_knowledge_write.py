import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from assistent import create_app
from assistent.config import Settings
from assistent.application.answer_sources import AgentReply
from assistent.infrastructure.tools.knowledge_write import create_knowledge_write_tool
from assistent.application.agent_service import AgentService
from assistent.application.graph_builder import build_agent_graph
from langchain_core.messages import AIMessage
from test_required_retrieval import NoToolCallsModel

ADMIN = {'configurable': {'user_context': {'user_id': 'admin-user', 'role': 'admin'},
                          'user_message': 'Сохрани в общую базу знаний: проверенный факт'}}


def client_tool(monkeypatch, response):
    requests = []
    def respond(request):
        requests.append(request)
        return response(request)
    factory = httpx.AsyncClient
    monkeypatch.setattr('assistent.infrastructure.tools.knowledge_write.httpx.AsyncClient',
        lambda **kw: factory(transport=httpx.MockTransport(respond), **kw))
    settings = Settings(_env_file=None, documents_url='http://documents', documents_admin_key='write-key')
    return create_knowledge_write_tool(settings), requests


@pytest.mark.asyncio
async def test_saves_shared_internal_document_with_trusted_owner(monkeypatch):
    tool, requests = client_tool(monkeypatch, lambda _: httpx.Response(201,
        json={'status': 'indexed', 'document_id': 'doc-'+'a'*32, 'chunks_count': 2}))
    result = await tool.ainvoke({'title': ' Fact ', 'content': ' Verified fact '}, config=ADMIN)
    assert json.loads(result)['chunks_count'] == 2
    payload = json.loads(requests[0].content)
    assert payload['owner_id'] == 'admin-user'
    assert payload['content'] == 'Verified fact'
    assert payload['is_public'] is False and payload['allowed_departments'] == []
    assert set(payload['allowed_roles']) == {'employee', 'manager', 'programmer', 'admin'}
    assert requests[0].url.path == '/api/v1/documents'
    assert requests[0].headers['X-Internal-Token'] == 'write-key'
    assert tool.metadata['allowed_roles'] == ['admin']


@pytest.mark.asyncio
async def test_refuses_non_admin_and_unrequested_writes(monkeypatch):
    tool, requests = client_tool(monkeypatch, lambda _: (_ for _ in ()).throw(AssertionError('No network expected')))
    for role in ('employee', 'manager', 'programmer'):
        result = await tool.ainvoke({'title': 'Fact', 'content': 'Fact'}, config={
            'configurable': {**ADMIN['configurable'], 'user_context': {'user_id': 'u', 'role': role}}})
        assert 'только администратор' in result
    for question in ('Прочитай документ', 'Не сохраняй это', 'Объясни текст: сохрани это в БЗ'):
        result = await tool.ainvoke({'title': 'Fact', 'content': 'Fact'}, config={
            'configurable': {**ADMIN['configurable'], 'user_message': question}})
        assert 'не выполнена' in result
    assert not requests


@pytest.mark.asyncio
async def test_never_claims_success_on_failed_or_unconfirmed_indexing(monkeypatch):
    tool, _ = client_tool(monkeypatch, lambda _: httpx.Response(201,
        json={'status': 'processing', 'document_id': 'doc-'+'a'*32, 'chunks_count': 0}))
    result = await tool.ainvoke({'title': 'Fact', 'content': 'Fact'}, config=ADMIN)
    assert 'не подтвердил' in result


@pytest.mark.asyncio
async def test_graph_carries_verified_save_receipt_to_api_result(monkeypatch):
    tool, requests = client_tool(monkeypatch, lambda _: httpx.Response(201,
        json={'status': 'indexed', 'document_id': 'doc-'+'a'*32, 'chunks_count': 1}))
    model = NoToolCallsModel(messages=iter([
        AIMessage(content='', tool_calls=[{'name': 'save_knowledge', 'args': {'title': 'Fact', 'content': 'Verified fact'}, 'id': 'save-1'}]),
        AIMessage(content='Saved'),
    ]))
    service = AgentService(build_agent_graph(model, [tool], system_prompt='Test', model_first_tools=True, task_routing=False))
    reply, _ = await service.chat('Сохрани в общую БЗ факт', user_context=ADMIN['configurable']['user_context'])
    assert reply.saved_documents == [{'title': 'Fact', 'document_id': 'doc-'+'a'*32, 'chunks_count': 1}]
    assert len(requests) == 1


def test_write_receipt_survives_access_epoch_change_without_leaking_old_answer(monkeypatch):
    monkeypatch.setattr('assistent.api.router.access_revision', AsyncMock(side_effect=['a'*32, 'b'*32]))
    receipt = {'document_id': 'doc-'+'a'*32, 'title': 'Shared fact', 'chunks_count': 1}
    service = AsyncMock()
    service.chat.return_value = (AgentReply('OLD PRIVATE DATA', saved_documents=[receipt]), 'thread')
    settings = Settings(_env_file=None, secure_runtime=True, chat_api_key='c'*40, documents_read_key='r'*40)
    app = create_app(settings, agent_service=service)
    response = TestClient(app).post('/api/v1/chat', headers={'X-Internal-Token': 'c'*40},
        json={'message': 'Сохрани в общую БЗ факт', 'user_context': {'user_id': 'u', 'role': 'admin'}})
    assert response.status_code == 200
    assert 'Сохранено' in response.json()['reply'] and 'OLD PRIVATE DATA' not in response.text
