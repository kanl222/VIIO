from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.runnables import RunnableConfig

from apiagent.config import Settings
from apiagent.services.database_agent import DatabaseAgentService
from apiagent.tools.postgres_tool import create_postgres_readonly_tool, PostgresMCPClient


def tool_fixture(enabled=True):
    settings = Settings(_env_file=None, pgsql_enabled=enabled, pgsql_max_rows=3)
    client = MagicMock()
    client.call = AsyncMock(return_value='[{"id": 1}]')
    return create_postgres_readonly_tool(settings, client), client


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["employee", "manager", "guest", "", "administrator"])
async def test_disallowed_roles_never_access_backend(role):
    tool, client = tool_fixture()
    result = await tool.ainvoke({"operation": "query", "sql": "SELECT 1"}, config={"configurable": {"user_context": {"user_id": "u", "role": role}}})
    assert "только программистам" in result
    client.call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["programmer", "admin"])
async def test_permitted_roles_get_bounded_query(role):
    tool, client = tool_fixture()
    result = await tool.ainvoke({"operation": "query", "sql": "SELECT id FROM orders;"}, config={"configurable": {"user_context": {"user_id": "u", "role": role}}})
    assert result == '[{"id": 1}]'
    client.call.assert_awaited_once_with('execute_sql', {'sql': 'SELECT * FROM (SELECT id FROM orders) AS viio_readonly_result LIMIT 3'})


@pytest.mark.asyncio
@pytest.mark.parametrize("context", [None, {"role": "admin"}])
async def test_identity_required(context):
    tool, client = tool_fixture()
    await tool.ainvoke({"operation": "schemas"}, config={"configurable": {"user_context": context}})
    client.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_and_backend_errors_do_not_reveal_credentials():
    tool, client = tool_fixture(False)
    config = {"configurable": {"user_context": {"user_id": "u", "role": "admin"}}}
    assert "выключен" in await tool.ainvoke({"operation": "schemas"}, config=config)
    client.call.assert_not_awaited()
    tool, client = tool_fixture()
    client.call.side_effect = RuntimeError('postgres://user:private-password@host/db')
    result = await tool.ainvoke({"operation": "schemas"}, config=config)
    assert "private-password" not in result
    assert "недоступен" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("command, operation", [("schemas", "schemas"), ("tables public", "tables"), ("describe public.orders", "describe"), ("SELECT 1", "query")])
async def test_explicit_database_commands_bypass_coder_and_model(command, operation):
    tool, client = tool_fixture()
    general, model = MagicMock(), MagicMock()
    service = DatabaseAgentService(general, model, tool, True)
    result, tid = await service.chat('/db '+command, 'same-chat', {"user_id": "u", "role": "admin"})
    assert result == '[{"id": 1}]' and tid == 'same-chat'
    general.chat.assert_not_called()
    model.bind_tools.assert_not_called()
    client.call.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_prefix_is_exact_and_ordinary_requests_are_unchanged():
    general = MagicMock()
    general.chat = AsyncMock(return_value=('normal', 't'))
    tool, client = tool_fixture()
    service = DatabaseAgentService(general, MagicMock(), tool, True)
    assert await service.chat('/dbx SELECT 1', 't', {}) == ('normal', 't')
    general.chat.assert_awaited_once()
    client.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_database_wrapper_checks_roles_before_model_or_backend():
    tool, client = tool_fixture()
    model = MagicMock()
    service = DatabaseAgentService(MagicMock(), model, tool, True)
    result, _ = await service.chat('/db покажи заказы', 't', {"user_id": "u", "role": "employee"})
    assert "только программистам" in result
    model.bind_tools.assert_not_called()
    client.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_disallows_arbitrary_upstream_tools():
    client = PostgresMCPClient(Settings(_env_file=None))
    with pytest.raises(ValueError):
        await client.call('analyze_workload_indexes', {})


@pytest.mark.asyncio
async def test_explain_never_enables_analyze_or_adds_a_row_limit():
    tool, client = tool_fixture()
    await tool.ainvoke({'operation':'explain','sql':'SELECT * FROM orders;'}, config={'configurable':{'user_context':{'user_id':'u','role':'programmer'}}})
    client.call.assert_awaited_once_with('explain_query', {'sql':'SELECT * FROM (SELECT * FROM orders) AS viio_explain','analyze':False,'hypothetical_indexes':[]})


@pytest.mark.asyncio
async def test_explain_execution_cannot_be_enabled_through_the_adapter():
    client = PostgresMCPClient(Settings(_env_file=None))
    for args in [{'sql':'SELECT 1','analyze':True}, {'sql':'SELECT 1'}, {'sql':'SELECT 1','analyze':False,'hypothetical_indexes':[{}]}]:
        with pytest.raises(ValueError):
            await client.call('explain_query', args)
