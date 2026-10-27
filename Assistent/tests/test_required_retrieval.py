from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from assistent.services.agent_service import AgentService
from assistent.services.graph_builder import build_agent_graph, messages_with_retrieved_context
from viio_documents.tools.search_tool import create_knowledge_search_tool


class NoToolCallsModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.asyncio
async def test_retrieval_runs_before_model_even_when_model_never_calls_tools():
    store = MagicMock()
    store.similarity_search.return_value = [Document(
        page_content='ИСЛО используется для работы с лекарственным обеспечением.',
        metadata={'title': 'ИСЛО', 'document_id': 'doc-islo'},
    )]
    model = NoToolCallsModel(messages=iter([AIMessage(content='Ответ без вызова инструментов')]))
    graph = build_agent_graph(model, [create_knowledge_search_tool(store)], MemorySaver())
    service = AgentService(graph)
    await service.chat('Что такое ИСЛО?', thread_id='required-search', user_context={'role': 'employee', 'departments': []})
    store.similarity_search.assert_called_once()
    assert store.similarity_search.call_args.kwargs['filter'] is not None
    state = await graph.aget_state({'configurable': {'thread_id': 'required-search'}})
    tools = [m for m in state.values['messages'] if isinstance(m, ToolMessage)]
    assert len(tools) == 1
    assert 'doc-islo' in tools[0].content


@pytest.mark.asyncio
async def test_each_followup_gets_fresh_retrieval_with_current_user_context():
    store = MagicMock()
    store.similarity_search.return_value = []
    model = NoToolCallsModel(messages=iter([AIMessage(content='Первый ответ'), AIMessage(content='Второй ответ')]))
    graph = build_agent_graph(model, [create_knowledge_search_tool(store)], MemorySaver())
    service = AgentService(graph)
    await service.chat('Что такое ИСЛО?', 'followup', {'role': 'admin'})
    await service.chat('Как создать отчёт?', 'followup', {'role': 'employee', 'departments': []})
    assert store.similarity_search.call_count == 2
    assert store.similarity_search.call_args_list[0].kwargs['filter'] is None
    assert store.similarity_search.call_args_list[1].kwargs['filter'] is not None
    assert 'Как создать отчёт?' in store.similarity_search.call_args.kwargs['query']


def test_context_is_in_current_question_without_mutating_checkpoint_messages():
    question = HumanMessage(content='Что такое ИСЛО?')
    call = AIMessage(content='', tool_calls=[{
        'name': 'search_knowledge_base', 'args': {'query': question.content}, 'id': 'knowledge-current',
    }])
    context = ToolMessage(content='<context>Лекарственное обеспечение</context>', tool_call_id='knowledge-current')
    prepared = messages_with_retrieved_context([question, call, context])
    assert len(prepared) == 1
    assert 'Лекарственное обеспечение' in prepared[0].content
    assert prepared[0].content.endswith('Что такое ИСЛО?')
    assert question.content == 'Что такое ИСЛО?'


def test_old_automatic_context_is_removed_but_model_tool_protocol_is_preserved():
    messages = [
        HumanMessage(content='Старый вопрос'),
        AIMessage(content='', tool_calls=[{'name': 'search_knowledge_base', 'args': {}, 'id': 'knowledge-old'}]),
        ToolMessage(content='Старые закрытые сведения', tool_call_id='knowledge-old'),
        AIMessage(content='Предыдущий ответ'),
        HumanMessage(content='Новый вопрос'),
        AIMessage(content='', tool_calls=[{'name': 'search_knowledge_base', 'args': {}, 'id': 'knowledge-new'}]),
        ToolMessage(content='Ничего не найдено', tool_call_id='knowledge-new'),
        AIMessage(content='', tool_calls=[{'name': 'other_tool', 'args': {}, 'id': 'model-call'}]),
        ToolMessage(content='Дополнительный результат', tool_call_id='model-call'),
    ]
    prepared = messages_with_retrieved_context(messages)
    assert 'Старые закрытые сведения' not in str(prepared)
    assert 'Ничего не найдено' in prepared[2].content
    assert prepared[-2:] == messages[-2:]
