import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from assistent.services.documents_client import create_document_list_tool, create_remote_search_tool
from assistent.services.graph_builder import build_agent_graph, messages_with_retrieved_context
from assistent.services.agent_service import AgentService


class DirectoryModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.asyncio
async def test_catalog_parallel_calls_are_bounded_and_sources_survive():
    calls = []

    @tool('list_documents', response_format='content_and_artifact')
    async def directory(offset: int = 0):
        """List documents."""
        calls.append(offset)
        return 'Guide', {'sources': [{'title': 'Guide', 'document_id': 'guide'}]}

    @tool('search_document_content')
    async def search(query: str):
        """Read document content."""
        raise AssertionError('Catalog question must not search contents')

    model = DirectoryModel(messages=iter([
        AIMessage(content='', tool_calls=[
            {'name': 'list_documents', 'args': {'offset': offset}, 'id': f'call-{offset}'}
            for offset in (0, 20)]),
        AIMessage(content='[Guide](/api/chat/download/guide) (doc_id: guide)'),
    ]))
    graph = build_agent_graph(model, [directory, search], MemorySaver(),
                              model_first_tools=True, task_routing=False, max_tool_calls=6)
    reply, _ = await AgentService(graph).chat('Какие документы доступны?', user_context={'role': 'employee'})
    assert calls == [0]
    assert reply.sources == [{'title': 'Guide', 'document_id': 'guide'}]
    assert reply == '[Guide](/api/chat/download/guide)'


@pytest.mark.asyncio
@pytest.mark.parametrize('citation', [
    'Руководство пользователя ИС ЛО',
    'Инструкция по ИС ЛО (doc_id: doc-guide)',
])
async def test_search_keeps_the_source_card_when_model_uses_a_readable_name(citation):
    @tool('search_knowledge_base', response_format='content_and_artifact')
    async def search(query: str):
        """Read document content."""
        from assistent.services.answer_sources import readable_reference, sources_from_reference
        reference = (
            '<context source="06_is_lo_rukovodstvo_polzovatelya" doc_id="doc-guide">'
            '# Руководство пользователя ИС ЛО\nНажмите «Продолжить».</context>'
        )
        return readable_reference(reference, include_ids=True), {'sources': sources_from_reference(reference)}

    model = DirectoryModel(messages=iter([
        AIMessage(content='', tool_calls=[
            {'name': 'search_knowledge_base', 'args': {'query': 'сертификат'}, 'id': 'search'}]),
        AIMessage(content=f'Нажмите «Продолжить».\n\nИсточники:\n• {citation}'),
    ]))
    graph = build_agent_graph(model, [search], model_first_tools=True, task_routing=False)

    reply, _ = await AgentService(graph).chat('Что нажать после выбора сертификата?')

    assert reply.sources == [{'title': 'Руководство пользователя ИС ЛО', 'document_id': 'doc-guide'}]
    assert 'doc-guide' not in reply


@pytest.mark.asyncio
async def test_remote_tools_keep_identity_out_of_model_arguments(monkeypatch):
    requests = []

    def respond(request):
        requests.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith('/documents'):
            return httpx.Response(200, json={'items': [{'title': 'Guide', 'document_id': 'one'}],
                'total': 1, 'next_offset': None, 'untagged_count': 0})
        return httpx.Response(200, json={'context': '<context source="Guide" doc_id="one">Steps</context>'})

    client_type = httpx.AsyncClient
    monkeypatch.setattr('assistent.services.documents_client.httpx.AsyncClient',
                        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs))
    settings = SimpleNamespace(documents_url='http://documents', documents_read_key='read',
                               internal_api_key='', secure_runtime=True, rag_context_max_chars=12000)
    config = {'configurable': {'user_context': {'role': 'employee', 'user_id': 'u', '_user_notes': 'PRIVATE'}}}
    directory = create_document_list_tool(settings)
    assert 'user_context' not in directory.args
    result = await directory.ainvoke({'tags': ['инструкция']}, config=config)
    assert json.loads(result)['total'] == 1
    search = create_remote_search_tool(settings, tool_name='search_document_content')
    result = await search.ainvoke({'query': 'Steps', 'document_ids': ['one']}, config=config)
    assert 'doc_id="one"' in result
    assert requests[1][1]['document_ids'] == ['one']
    assert requests[0][1]['user_context'] == {'role': 'employee', 'user_id': 'u'}

    search = create_remote_search_tool(settings)
    result = await search.ainvoke({
        'type': 'tool_call', 'id': 'search-1', 'name': 'search_knowledge_base',
        'args': {'query': 'Steps'},
    }, config=config)
    assert 'doc_id: one' in result.content
    assert result.artifact['sources'] == [{'title': 'Guide', 'document_id': 'one'}]


def test_model_context_bounds_search_excerpts_and_drops_previous_db_results():
    names = ['postgresql_readonly', 'search_knowledge_base', 'search_knowledge_base']
    calls = [AIMessage(content='', tool_calls=[{'id': f'call-{i}', 'name': name, 'args': {}}])
             for i, name in enumerate(names)]
    history = [
        HumanMessage(content='Сколько записей в БД?'), calls[0],
        ToolMessage(content='previous rows', name=names[0], tool_call_id='call-0'),
        AIMessage(content='Краткий итог проверки БД.'),
        HumanMessage(content='Найди руководство.'),
    ]
    for i in (1, 2):
        history.extend([calls[i], ToolMessage(name=names[i], tool_call_id=f'call-{i}', content=(
            f'<context source="Guide {i}" doc_id="doc-{i}">{"x" * 1000}</context>'
        ))])

    prepared = messages_with_retrieved_context(history, context_max_chars=200)
    results = [message.content for message in prepared if isinstance(message, ToolMessage)]
    assert len(results) == 2 and sum(map(len, results)) <= 200
    assert all(f'doc_id: doc-{i}' in result for i, result in enumerate(results, 1))
    assert any(message.content == 'Краткий итог проверки БД.' for message in prepared)
    assert not any('previous rows' in str(message.content) for message in prepared)
