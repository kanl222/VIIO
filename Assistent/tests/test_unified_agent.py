from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from assistent import create_app
from assistent.config import Settings
from assistent.prompts import UNIFIED_SYSTEM_PROMPT
from assistent.services.agent_service import AgentService
from assistent.services.graph_builder import build_agent_graph
from assistent.tools.github_tool import create_github_tool
from assistent.tools.postgres_tool import create_postgres_readonly_tool
from test_required_retrieval import NoToolCallsModel


class RecordingModel(NoToolCallsModel):
    exposed: list = []
    inputs: list = []

    def bind_tools(self, tools, **kwargs):
        self.exposed.append([item.name for item in tools])
        return self

    async def _agenerate(self, messages, **kwargs):
        self.inputs.append(messages)
        return await super()._agenerate(messages, **kwargs)


@tool
def search_knowledge_base(query: str) -> str:
    """Search permitted knowledge."""
    return 'Knowledge result'


@pytest.mark.asyncio
async def test_tools_filtered_per_request_and_role_downgrade_clears_history():
    settings = Settings(_env_file=None, github_mcp_enabled=True, github_repository='*',
                        github_mcp_token='test', pgsql_enabled=True)
    model = RecordingModel(messages=iter([AIMessage(content='Answer') for _ in range(5)]))
    service = AgentService(build_agent_graph(model, [search_knowledge_base,
        create_github_tool(settings), create_postgres_readonly_tool(settings)],
        checkpointer=MemorySaver(), system_prompt=UNIFIED_SYSTEM_PROMPT,
        model_first_tools=True, task_routing=False))
    await service.chat('Privileged context', 'same', {'user_id': 'u', 'role': 'admin'})
    assert set(model.exposed[-1]) == {'search_knowledge_base', 'github_repository', 'postgresql_readonly'}
    await service.chat('Employee question', 'same', {'user_id': 'u', 'role': 'employee'})
    assert model.exposed[-1] == ['search_knowledge_base']
    assert not any(isinstance(m, HumanMessage) and m.content == 'Privileged context' for m in model.inputs[-1])
    await service.chat('Manager question', 'other', {'user_id': 'm', 'role': 'manager'})
    assert model.exposed[-1] == ['search_knowledge_base']
    await service.chat('Missing identity', 'guest', {'role': 'admin'})
    assert model.exposed[-1] == ['search_knowledge_base']
    await service.chat('Programmer question', 'programmer', {'user_id': 'p', 'role': 'programmer'})
    assert set(model.exposed[-1]) == {'search_knowledge_base', 'github_repository', 'postgresql_readonly'}


def test_app_builds_one_model_and_no_specialist_tools():
    settings = Settings(_env_file=None, github_mcp_enabled=True, github_repository='*',
                        github_mcp_token='test', pgsql_enabled=True, coding_agent_enabled=True)
    with patch('assistent.create_chat_model') as model, patch('assistent.build_agent_graph') as build:
        create_app(settings=settings, checkpointer=MemorySaver())
    model.assert_called_once_with(settings)
    build.assert_called_once()
    names = {item.name for item in build.call_args.kwargs['tools']}
    assert names == {'search_knowledge_base', 'list_documents', 'manage_plan', 'github_repository', 'postgresql_readonly'}
    assert build.call_args.kwargs['task_routing'] is False


def test_disabled_integrations_are_not_registered():
    with patch('assistent.create_chat_model'), patch('assistent.build_agent_graph') as build:
        create_app(settings=Settings(_env_file=None, github_mcp_enabled=False, pgsql_enabled=False))
    assert [item.name for item in build.call_args.kwargs['tools']] == ['search_knowledge_base', 'list_documents', 'manage_plan']
