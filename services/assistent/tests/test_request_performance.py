"""Regression checks for avoidable work, concurrency and retrieval boundaries."""

import asyncio
import threading
from unittest.mock import MagicMock

import httpx
import pytest
from langchain_core.embeddings import FakeEmbeddings
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse

from assistent.application.agent_service import AgentService
from assistent.application.coding_agent import CodingAgentService, is_coding_request
from assistent.application.graph_builder import build_agent_graph, messages_with_retrieved_context
from viio_rag.application.search_tool import create_knowledge_search_tool
from viio_rag.infrastructure.vector_store import QdrantVectorStoreAdapter
from test_required_retrieval import NoToolCallsModel


@pytest.mark.asyncio
@pytest.mark.parametrize('message', ['Привет!', 'Здравствуйте', 'Спасибо большое.', 'Hello!', '/code Напиши Python'])
async def test_social_messages_and_specialist_command_skip_automatic_retrieval(message):
    store = MagicMock()
    model = NoToolCallsModel(messages=iter([AIMessage(content='Ответ')]))
    graph = build_agent_graph(model, [create_knowledge_search_tool(store)], MemorySaver())
    await AgentService(graph).chat(message, 'skip', {'role': 'employee'})
    store.similarity_search.assert_not_called()


@pytest.mark.asyncio
async def test_greeting_with_domain_question_still_retrieves():
    store = MagicMock()
    store.similarity_search.return_value = []
    model = NoToolCallsModel(messages=iter([AIMessage(content='Ответ')]))
    graph = build_agent_graph(model, [create_knowledge_search_tool(store)], MemorySaver())
    await AgentService(graph).chat('Привет! Как оформить отпуск?', 'question', {'role': 'employee'})
    store.similarity_search.assert_called_once()


@pytest.mark.parametrize('message', [
    'Оптимизировать отчёт о продажах', 'Ускорь подготовку отчёта',
    'Оптимизируй запрос на отпуск', 'Оптимизируй работу отдела',
])
def test_business_optimization_is_not_coding(message):
    assert not is_coding_request(message)


@pytest.mark.parametrize('message', ['Оптимизируй SQL запрос', 'Ускорь код', 'Оптимизируй алгоритм сортировки'])
def test_technical_optimization_remains_coding(message):
    assert is_coding_request(message)


@pytest.mark.asyncio
@pytest.mark.parametrize('second_user,second_thread', [('u2', 'same'), ('u1', 'different')])
async def test_independent_coder_histories_do_not_queue(second_user, second_thread):
    coder = CodingAgentService(MagicMock(), ['programmer'])
    both_started = asyncio.Event()
    release = asyncio.Event()
    active = 0

    async def answer(*args):
        nonlocal active
        active += 1
        if active == 2:
            both_started.set()
        await release.wait()
        return 'code', args[1]

    coder._answer = answer
    tasks = [asyncio.create_task(coder.chat('code', tid, {'user_id': uid, 'role': 'programmer'}))
             for uid, tid in [('u1', 'same'), (second_user, second_thread)]]
    try:
        await asyncio.wait_for(both_started.wait(), 3)
    finally:
        release.set()
        await asyncio.gather(*tasks)
    assert not coder._locks


@pytest.mark.asyncio
async def test_same_coder_history_serializes_and_cancellation_releases_waiter():
    coder = CodingAgentService(MagicMock(), ['programmer'])
    entered = asyncio.Event()
    release = asyncio.Event()
    active = peak = 0

    async def answer(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        await release.wait()
        active -= 1
        return 'code', args[1]

    coder._answer = answer
    context = {'user_id': 'u', 'role': 'programmer'}
    first = asyncio.create_task(coder.chat('first', 'same', context))
    await asyncio.wait_for(entered.wait(), 3)
    waiting = asyncio.create_task(coder.chat('cancel', 'same', context))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    next_call = asyncio.create_task(coder.chat('next', 'same', context))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, next_call)
    assert peak == 1 and not coder._locks


@pytest.mark.asyncio
async def test_sync_search_in_tool_node_does_not_block_event_loop():
    loop = asyncio.get_running_loop()
    event_loop_thread = threading.get_ident()
    started = asyncio.Event()
    release = threading.Event()
    workers = []
    store = MagicMock()

    def search(**kwargs):
        workers.append(threading.get_ident())
        loop.call_soon_threadsafe(started.set)
        release.wait(timeout=3)
        return []

    store.similarity_search.side_effect = search
    model = NoToolCallsModel(messages=iter([AIMessage(content='Ответ')]))
    graph = build_agent_graph(model, [create_knowledge_search_tool(store)], MemorySaver())
    task = asyncio.create_task(AgentService(graph).chat('Какой регламент?', 'nonblocking', {'role': 'employee'}))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert not release.is_set()
        assert workers and workers[0] != event_loop_thread
        assert not task.done()
    finally:
        release.set()
        await task
    assert store.similarity_search.call_args.kwargs['filter'] is not None


def test_search_context_has_shared_budget_preserves_question_and_checkpoint():
    question = HumanMessage(content='Текущий вопрос')
    calls = AIMessage(content='', tool_calls=[
        {'name': 'search_knowledge_base', 'args': {}, 'id': 'knowledge-one'},
        {'name': 'search_knowledge_base', 'args': {}, 'id': 'knowledge-two'},
    ])
    results = [ToolMessage(content='x' * 10000, tool_call_id='knowledge-one'),
               ToolMessage(content='y' * 10000, tool_call_id='knowledge-two')]
    prepared = messages_with_retrieved_context([question, calls, *results], context_max_chars=1234)
    injected = prepared[0].content.split('(справочные данные):\n\n', 1)[1].split('\n\nОтветьте', 1)[0]
    assert len(injected) <= 1234
    assert prepared[0].content.endswith(question.content)
    assert question.content == 'Текущий вопрос' and len(results[0].content) == 10000


def test_retrieval_history_walk_has_linear_access_count():
    class CountedHistory(list):
        visits = 0
        def __iter__(self):
            for item in super().__iter__():
                self.visits += 1
                yield item

    history = CountedHistory([HumanMessage(content='Вопрос'), AIMessage(content='Ответ')] * 1000)
    messages_with_retrieved_context(history)
    assert history.visits <= 4 * len(history)


def test_qdrant_search_does_not_preflight_existence_and_retains_title_lookup(monkeypatch):
    client = QdrantClient(':memory:')
    adapter = QdrantVectorStoreAdapter(client, FakeEmbeddings(size=8), 'performance')
    adapter.add_texts(['Описание системы'], [{'title': 'ИСЛО', 'document_id': 'd'}])
    existence = MagicMock(side_effect=AssertionError('Search must not check collection_exists'))
    query = MagicMock(wraps=client.query_points)
    scroll = MagicMock(wraps=client.scroll)
    monkeypatch.setattr(client, 'collection_exists', existence)
    monkeypatch.setattr(client, 'query_points', query)
    monkeypatch.setattr(client, 'scroll', scroll)
    assert adapter.similarity_search('Как оформить отпуск?')
    assert query.call_count == 1 and scroll.call_count == 0
    assert adapter.similarity_search('Что такое ИСЛО?')
    assert query.call_count == 2 and scroll.call_count == 1
    existence.assert_not_called()


def test_missing_collection_is_not_negatively_cached():
    client = QdrantClient(':memory:')
    adapter = QdrantVectorStoreAdapter(client, FakeEmbeddings(size=8), 'late')
    assert adapter.similarity_search('Что такое ИСЛО?') == []
    adapter.add_texts(['Описание'], [{'title': 'ИСЛО'}])
    assert adapter.similarity_search('Что такое ИСЛО?')
    client.delete_collection('late')
    assert adapter.similarity_search('Описание системы') == []


@pytest.mark.parametrize('status', [403, 404, 500])
def test_only_missing_collection_response_is_handled_as_empty(monkeypatch, status):
    adapter = QdrantVectorStoreAdapter(QdrantClient(':memory:'), FakeEmbeddings(size=8), 'errors')
    error = UnexpectedResponse(status, 'failure', b'{}', httpx.Headers())
    monkeypatch.setattr(adapter._store, 'similarity_search', MagicMock(side_effect=error))
    if status == 404:
        assert adapter.similarity_search('query') == []
    else:
        with pytest.raises(UnexpectedResponse):
            adapter.similarity_search('query')
