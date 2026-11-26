import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from assistent.config import Settings
from assistent.api.router import create_api_router
from assistent.application.agent_service import AgentService
from assistent.application.graph_builder import build_agent_graph
from assistent.application.streaming import event_sink
from assistent.infrastructure.tools.planning import manage_plan
from test_required_retrieval import NoToolCallsModel


@pytest.mark.asyncio
async def test_real_graph_streams_text_and_returns_same_final_answer():
    events = []
    async def emit(event): events.append(event)
    service = AgentService(build_agent_graph(NoToolCallsModel(messages=iter([AIMessage(content='Привет мир')])),
        system_prompt='Test', model_first_tools=True, task_routing=False))
    token = event_sink.set(emit)
    try:
        reply, _ = await service.chat('Hello', user_context={'user_id': 'u', 'role': 'employee'})
    finally:
        event_sink.reset(token)
    assert reply == 'Привет мир'
    assert ''.join(e['text'] for e in events if e['type'] == 'delta') == 'Привет мир'
    assert any(e['type'] == 'reset' for e in events)


@pytest.mark.asyncio
async def test_plan_updates_are_streamed_as_user_visible_steps():
    events = []
    async def emit(event): events.append(event)
    steps = [{'title': 'Проверить входные данные', 'status': 'in_progress'}]
    class ToolStreamingModel(NoToolCallsModel):
        async def _astream(self, messages, **kwargs):
            message = next(self.messages)
            yield ChatGenerationChunk(message=AIMessageChunk(content=message.content, tool_calls=message.tool_calls))
    model = ToolStreamingModel(messages=iter([
        AIMessage(content='', tool_calls=[{'name': 'manage_plan', 'args': {'steps': steps}, 'id': 'plan-1'}]),
        AIMessage(content='Готово'),
    ]))
    service = AgentService(build_agent_graph(model, [manage_plan], system_prompt='Test', model_first_tools=True,
                                             task_routing=False, max_tool_calls=24))
    token = event_sink.set(emit)
    try:
        await service.chat('Выполни задачу из нескольких шагов')
    finally:
        event_sink.reset(token)
    assert any(e['type'] == 'plan' and e['steps'] == steps for e in events)


def test_sse_route_streams_deltas_and_requires_service_key():
    class Service:
        async def chat(self, message, thread_id=None, user_context=None):
            sink = event_sink.get()
            await sink({'type': 'delta', 'text': 'Привет'})
            return 'Привет', 'thread'
    app = FastAPI()
    app.include_router(create_api_router(Service(), Settings(_env_file=None, chat_api_key='test-key')))
    client = TestClient(app)
    assert client.post('/api/v1/chat/stream', json={'message': 'Hello'}).status_code == 401
    response = client.post('/api/v1/chat/stream', headers={'X-Internal-Token': 'test-key'}, json={'message': 'Hello'})
    assert response.status_code == 200
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert any(e['type'] == 'delta' and e['text'] == 'Привет' for e in events)
    assert events[-1]['type'] == 'done' and events[-1]['reply'] == 'Привет'


def test_sse_revocation_suppresses_provisional_and_final_private_text(monkeypatch):
    calls = 0
    async def revision(_):
        nonlocal calls
        calls += 1
        return 'a'*32 if calls < 3 else 'b'*32
    monkeypatch.setattr('assistent.api.router.access_revision', revision)
    class Service:
        async def chat(self, *args):
            await event_sink.get()({'type': 'delta', 'text': 'PRIVATE TEXT'})
            return 'PRIVATE TEXT', 'thread'
    app = FastAPI()
    app.include_router(create_api_router(Service(), Settings(_env_file=None, secure_runtime=True,
        chat_api_key='c'*40, documents_read_key='r'*40)))
    response = TestClient(app).post('/api/v1/chat/stream', headers={'X-Internal-Token': 'c'*40}, json={'message': 'Question'})
    assert response.status_code == 200
    assert 'PRIVATE TEXT' not in response.text
    assert 'Доступ к материалам изменился' in response.text
