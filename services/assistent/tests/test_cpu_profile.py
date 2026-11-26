import asyncio
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from fastapi.testclient import TestClient
from assistent import create_app
from assistent.config import Settings
from assistent.application.cpu_knowledge import build_cpu_knowledge
from assistent.application.agent_service import AgentService
from assistent.application.streaming import event_sink
from assistent.infrastructure.tools.graph_tool import create_graph_search_tool
from test_required_retrieval import NoToolCallsModel


@pytest.mark.asyncio
async def test_cpu_retrieves_once_without_binding_tools_and_streams():
    contexts = []
    @tool('search_knowledge_base')
    async def search(query: str, config: RunnableConfig) -> str:
        """Search documents."""
        contexts.append(config['configurable']['user_context'])
        return 'Company fact'
    class Model(NoToolCallsModel):
        def bind_tools(self, *args, **kwargs):
            raise AssertionError('CPU QA must not bind tool schemas')
    graph = build_cpu_knowledge(Model(messages=iter([AIMessage(content='Ответ')])), [search], 3000)
    events = []
    async def emit(event): events.append(event)
    token = event_sink.set(emit)
    try:
        reply, _ = await AgentService(graph).chat('Что такое ИСЛО?', user_context={'user_id': 'u', 'role': 'employee'})
    finally:
        event_sink.reset(token)
    assert reply == 'Ответ'
    assert len(contexts) == 1 and contexts[0]['user_id'] == 'u'
    assert ''.join(event['text'] for event in events if event['type'] == 'delta') == 'Ответ'


@pytest.mark.asyncio
async def test_empty_cpu_answer_is_an_error():
    graph = build_cpu_knowledge(NoToolCallsModel(messages=iter([AIMessage(content='')])), [], 3000)
    with pytest.raises(RuntimeError):
        await AgentService(graph).chat('Привет')


def test_graph_tool_schema_does_not_expose_runtime_config():
    graph_tool = create_graph_search_tool(Settings(_env_file=None, memgraph_enabled=True))
    schema = graph_tool.tool_call_schema.model_json_schema()
    assert 'config' not in schema.get('properties', {})
    assert 'RunnableConfig' not in str(schema)


def test_cpu_profile_caps_budgets_and_disables_model_routing():
    with patch('assistent.create_chat_model'), patch('assistent.build_agent_graph') as build:
        app = create_app(Settings(_env_file=None, cpu_profile=True, routing_model='router', chat_temperature=0.8))
    settings = app.state.settings
    assert settings.chat_max_tokens == 384
    assert settings.chat_temperature == 0.0
    assert settings.rag_context_max_chars == 3000
    assert build.call_args.kwargs['routing_llm'] is None
    assert build.call_args.kwargs['cpu_profile'] is True


@pytest.mark.asyncio
async def test_api_serializes_generation():
    import httpx
    running = 0
    maximum = 0
    class Agent:
        async def chat(self, message, thread, context):
            nonlocal running, maximum
            running += 1
            maximum = max(maximum, running)
            await asyncio.sleep(.02)
            running -= 1
            return 'Ответ', 'thread'
    app = create_app(Settings(_env_file=None, chat_api_key='test'), agent_service=Agent())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        results = await asyncio.gather(*(client.post('/api/v1/chat', json={'message':'Привет'}, headers={'X-Internal-Token':'test'}) for _ in range(3)))
    assert all(result.status_code == 200 for result in results)
    assert maximum == 1


@pytest.mark.asyncio
async def test_schema_listing_uses_readonly_tool_without_model():
    from assistent.application.database_subgraph import build_database_analyst_graph
    operations = []
    @tool('postgresql_readonly')
    async def database(operation: str, config: RunnableConfig) -> str:
        """Read database metadata."""
        operations.append(operation)
        return '[{"schema_name":"public"}]'
    graph = build_database_analyst_graph(NoToolCallsModel(messages=iter([])), [database])
    result = await graph.ainvoke({'messages':[('user','Покажи список доступных схем базы данных.')]},
        context={'user_context':{'user_id':'u','role':'programmer'}})
    assert operations == ['schemas']
    assert 'public' in result['messages'][-1].content
