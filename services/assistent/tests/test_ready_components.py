import httpx
import pytest
from fastmcp import FastMCP
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.store.memory import InMemoryStore
from langgraph.checkpoint.memory import MemorySaver

from assistent.application.agent_service import AgentService
from assistent.application.graph_builder import build_agent_graph
from assistent.application.memory.personal_memory import PersonalFacts, migrate_legacy_facts
from assistent.infrastructure.tools.mcp_adapter import call_read_tool
from test_required_retrieval import NoToolCallsModel


@pytest.mark.asyncio
async def test_real_mcp_adapter_discovers_and_calls_only_allowed_tool(monkeypatch):
    from assistent.infrastructure.tools import mcp_adapter
    server = FastMCP('test')

    @server.tool()
    def read_code(path: str) -> str:
        return 'source at ' + path

    @server.tool()
    def delete_code() -> str:
        raise AssertionError('Write tool must never execute')

    monkeypatch.setattr(mcp_adapter, 'StreamableHttpTransport', lambda *args, **kwargs: server)
    text, failed = await call_read_tool('https://example.invalid/mcp', 'read_code',
        {'path': 'Program.cs'}, allowed={'read_code'}, timeout=10)
    assert 'source at Program.cs' in text and not failed
    with pytest.raises(ValueError):
        await call_read_tool('https://example.invalid/mcp', 'delete_code', {},
                            allowed={'read_code'}, timeout=10)


@pytest.mark.asyncio
async def test_store_preserves_owner_scope_dedup_and_forgetting():
    store = InMemoryStore()
    facts = PersonalFacts(store=store)
    owner = {'user_id': 'one', 'role': 'programmer'}
    other = {'user_id': 'two', 'role': 'programmer'}
    identifier = await facts.remember(owner, 'thread', 'Prefer Python')
    assert await facts.remember(owner, 'other-thread', 'Prefer Python') == identifier
    assert (await facts.list(owner))[0]['id'] == identifier
    assert await facts.list(other) == []
    assert await facts.list(dict(owner, role='employee')) == []
    assert not await facts.forget(other, identifier)
    assert await facts.forget(dict(owner, role='employee'), identifier)
    assert await facts.list(owner) == []
    with pytest.raises(PermissionError):
        await facts.list({})
    with pytest.raises(ValueError):
        await facts.remember(owner, 'thread', 'password=secret')


@pytest.mark.asyncio
async def test_store_legacy_migration_idempotence_and_no_fact_resurrection():
    store = InMemoryStore()
    facts = PersonalFacts(store=store)
    context = {'user_id': 'one', 'role': 'programmer'}
    from assistent.application.graph_builder import scope_key
    import uuid
    identifier = str(uuid.uuid4())
    rows = [dict(id=identifier, owner_id='one', access_scope=scope_key(context),
                 content='Old decision', source_thread='old', created_at='2026-01-01T00:00:00+00:00')]
    assert await migrate_legacy_facts(store, rows) == 1
    assert (await facts.list(context))[0]['id'] == identifier
    assert await facts.forget(context, identifier)
    assert await migrate_legacy_facts(store, rows) == 0
    assert await facts.list(context) == []


@pytest.mark.asyncio
async def test_store_limit_applies_across_owner_scopes():
    store = InMemoryStore()
    facts = PersonalFacts(store=store)
    for i in range(100):
        await store.aput(('user_facts', 'one', 'old-scope'), str(i),
                         {'content': str(i), 'created_at': str(i)})
    with pytest.raises(ValueError, match='100'):
        await facts.remember({'user_id': 'one', 'role': 'admin'}, 'thread', 'Another fact')


class TransientModel(FakeListChatModel):
    attempts: int = 0

    async def _agenerate(self, *args, **kwargs):
        self.attempts += 1
        if self.attempts == 1:
            raise httpx.ConnectError('temporary')
        return await super()._agenerate(*args, **kwargs)


@pytest.mark.asyncio
async def test_ready_model_retry_recovers_from_transient_failure():
    model = TransientModel(responses=['Recovered'])
    answer, _ = await AgentService(build_agent_graph(model, system_prompt='Test')).chat('Hello')
    assert answer == 'Recovered' and model.attempts == 2


@pytest.mark.asyncio
async def test_ready_tool_retry_preserves_one_tool_result():
    calls = []

    @tool
    async def remote_read() -> str:
        """Read a remote service."""
        calls.append(True)
        if len(calls) == 1:
            raise httpx.ConnectError('temporary')
        return 'Read succeeded'

    model = NoToolCallsModel(messages=iter([
        AIMessage(content='', tool_calls=[{'name': 'remote_read', 'args': {}, 'id': 'read-1'}]),
        AIMessage(content='Done'),
    ]))
    result = await build_agent_graph(model, [remote_read], system_prompt='Test').ainvoke(
        {'messages': [('user', 'Read service')]})
    assert len(calls) == 2
    assert len([m for m in result['messages'] if m.type == 'tool']) == 1


@pytest.mark.asyncio
async def test_ready_tool_limit_bounds_parallel_batch_and_resets_next_turn():
    calls = []

    @tool
    async def read_item(number: int) -> str:
        """Read one item."""
        calls.append(number)
        return str(number)

    batch = AIMessage(content='', tool_calls=[
        {'name': 'read_item', 'args': {'number': i}, 'id': f'item-{i}'} for i in range(6)])
    model = NoToolCallsModel(messages=iter([batch,
        AIMessage(content='', tool_calls=[{'name': 'read_item', 'args': {'number': 99}, 'id': 'next'}]),
        AIMessage(content='Done'),
    ]))
    service = AgentService(build_agent_graph(model, [read_item], MemorySaver(), system_prompt='Test'))
    answer, _ = await service.chat('Read a batch', 'same-thread')
    assert len(calls) <= 3 and 'три обращения' in answer
    answer, _ = await service.chat('Read another item', 'same-thread')
    assert 99 in calls and answer == 'Done'


@pytest.mark.asyncio
async def test_mcp_retries_only_transport_failures(monkeypatch):
    from assistent.infrastructure.tools import mcp_adapter
    from langchain_core.messages import ToolMessage
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    attempts = []

    class Adapter:
        def __init__(self, target):
            pass

        async def __aenter__(self):
            attempts.append(True)
            if len(attempts) == 1:
                raise httpx.ConnectError('temporary')
            return self

        async def __aexit__(self, *args):
            pass

        async def list_tools(self):
            return [SimpleNamespace(name='read_code', ainvoke=AsyncMock(return_value=
                ToolMessage(content='OK', tool_call_id='test')))]

    monkeypatch.setattr(mcp_adapter, 'MCPAdapter', Adapter)
    text, failed = await call_read_tool('https://example.invalid/mcp', 'read_code', {},
                                       allowed={'read_code'}, timeout=10)
    assert text == 'OK' and not failed and len(attempts) == 2
