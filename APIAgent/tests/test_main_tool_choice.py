from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from apiagent.services.agent_service import AgentService
from apiagent.services.answer_sources import cited_sources
from apiagent.services.database_agent import DatabaseAgentService, is_database_request
from apiagent.services.graph_builder import build_agent_graph
from apiagent.services.knowledge_data import excerpt
from apiagent.tools.search_tool import create_knowledge_search_tool
from test_required_retrieval import NoToolCallsModel
from test_database_agent import tool_fixture


@pytest.mark.asyncio
async def test_model_chooses_search_before_one_backend_call():
    store = MagicMock()
    store.similarity_search.return_value = [Document(page_content='active=7', metadata={'title': 'Статусы', 'document_id': 'd'})]
    model = NoToolCallsModel(messages=iter([
        AIMessage(content='', tool_calls=[{'name': 'search_knowledge_base', 'args': {'query': 'Статусы'}, 'id': 'model-search'}]),
        AIMessage(content='active=7. Источник: Статусы'),
    ]))
    graph = build_agent_graph(model, [create_knowledge_search_tool(store)], MemorySaver(), model_first_tools=True)
    answer, _ = await AgentService(graph).chat('Какие статусы у заявки?', 't', {'role': 'admin'})
    assert answer.sources == [{'title': 'Статусы', 'document_id': 'd'}]
    store.similarity_search.assert_called_once()
    messages = (await graph.aget_state({'configurable': {'thread_id': 't'}})).values['messages']
    assert [c['id'] for m in messages for c in getattr(m, 'tool_calls', [])] == ['model-search']


@pytest.mark.asyncio
async def test_unsupported_initial_guess_is_discarded_and_search_falls_back_once():
    store = MagicMock()
    store.similarity_search.return_value = []
    model = NoToolCallsModel(messages=iter([AIMessage(content='Руководитель Иван'), AIMessage(content='Сведения о проекте отсутствуют.')]))
    graph = build_agent_graph(model, [create_knowledge_search_tool(store)], MemorySaver(), model_first_tools=True)
    answer, _ = await AgentService(graph).chat('Кто руководит проектом Янтарь?', 't', {'role': 'admin'})
    assert 'Иван' not in answer and not answer.sources
    store.similarity_search.assert_called_once()


@pytest.mark.asyncio
async def test_tool_loop_stops_after_three_rounds():
    store = MagicMock()
    store.similarity_search.return_value = []
    responses = [AIMessage(content='', tool_calls=[{'name': 'search_knowledge_base', 'args': {'query': 'ИСЛО'}, 'id': f'model-{i}'}]) for i in range(3)]
    model = NoToolCallsModel(messages=iter([*responses, AIMessage(content='Недостаточно данных.')]))
    graph = build_agent_graph(model, [create_knowledge_search_tool(store)], model_first_tools=True)
    answer, _ = await AgentService(graph).chat('Что такое ИСЛО?')
    assert store.similarity_search.call_count == 3 and not answer.sources


def test_only_cited_candidates_are_returned_and_missing_answers_have_no_sources():
    sources = [{'title': 'Статусы', 'document_id': 'one'}, {'title': 'Отчёты', 'document_id': 'two'}]
    assert cited_sources('active=7. Источник: Статусы', sources) == sources[:1]
    assert cited_sources('Сведения о проекте в материалах отсутствуют. Источник: Статусы', sources) == []
    assert cited_sources('Я не знаю.', sources) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('role, enabled, expected', [('programmer', False, 'выключен'), ('employee', True, 'только программистам')])
async def test_natural_database_question_reports_capability_without_search(role, enabled, expected):
    general = MagicMock()
    tool, backend = tool_fixture(enabled)
    model = MagicMock()
    answer, _ = await DatabaseAgentService(general, model, tool, enabled).chat(
        'Какие таблицы есть в подключённой тестовой PostgreSQL?', 't', {'user_id': 'u', 'role': role})
    assert expected in answer
    general.chat.assert_not_called()
    model.bind_tools.assert_not_called()
    backend.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_natural_database_question_model_selects_read_tool_without_persistence():
    general = MagicMock()
    tool, backend = tool_fixture()
    model = NoToolCallsModel(messages=iter([
        AIMessage(content='', tool_calls=[{'name': 'postgresql_readonly', 'args': {'operation': 'tables'}, 'id': 'db-model'}]),
        AIMessage(content='Получены таблицы.'),
    ]))
    service = DatabaseAgentService(general, model, tool, True)
    answer, _ = await service.chat('Какие таблицы есть в подключённой PostgreSQL?', 't', {'user_id': 'u', 'role': 'admin'})
    assert answer == 'Получены таблицы.'
    backend.call.assert_awaited_once_with('list_objects', {'schema_name': 'public', 'object_type': 'table'})
    general.chat.assert_not_called()
    general.checkpointer.aget_tuple.assert_not_called()


@pytest.mark.parametrize('question', ['Напиши SQL SELECT из products', 'Что такое PostgreSQL?', 'Как получить список отчётов в ИСЛО?'])
def test_database_router_does_not_capture_code_theory_or_documents(question):
    assert not is_database_request(question)


def test_excerpt_keeps_list_method_instead_of_only_intro_and_single_lookup():
    text = 'Общее описание отчётов\n' + 'обзор\n' * 900 + 'Источник списка доступных отчётов: getTreeForUser()\n' + 'прочее\n' * 900
    selected, line = excerpt(text, 'Как получить список доступных отчётов?', limit=600)
    assert 'getTreeForUser()' in selected and len(selected) <= 600
    assert line > 1
