import pytest
from types import SimpleNamespace
from langchain_core.messages import AIMessage, ToolCall
from langgraph.checkpoint.memory import MemorySaver

from assistent.tools.graph_tool import create_graph_search_tool
from assistent.services.graph_builder import build_agent_graph
from assistent.services.agent_service import AgentService


@pytest.mark.asyncio
async def test_graph_search_tool_direct():
    settings = SimpleNamespace(memgraph_uri="bolt://127.0.0.1:7687", memgraph_user="", memgraph_password="")
    tool = create_graph_search_tool(settings)

    context = await tool.ainvoke({"query": "приемка лекарственных препаратов"})

    assert "Факты из графа знаний" in context
    assert "Приёмка" in context or "WMS" in context


@pytest.mark.asyncio
async def test_agent_graph_with_graph_tool():
    class FakeLLMWithToolCall:
        def __init__(self):
            self.calls = 0

        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        ToolCall(
                            name="search_knowledge_graph",
                            args={"query": "инвентаризация"},
                            id="call_graph_1",
                        )
                    ],
                )
            return AIMessage(content="Инвентаризация на складах находится в разделе Управление запасами.")

    settings = SimpleNamespace(memgraph_uri="bolt://127.0.0.1:7687", memgraph_user="", memgraph_password="")
    graph_tool = create_graph_search_tool(settings)
    fake_llm = FakeLLMWithToolCall()

    graph = build_agent_graph(
        llm=fake_llm,
        tools=[graph_tool],
        checkpointer=MemorySaver(),
        system_prompt="Test agent",
        model_first_tools=True,
    )
    service = AgentService(graph=graph)

    answer, _ = await service.chat(
        message="Где в программе находится инвентаризация?",
        thread_id="test_thread_graph",
    )

    assert "Инвентаризация на складах" in answer
    assert fake_llm.calls == 2


@pytest.mark.asyncio
async def test_graph_search_processes_and_steps():
    settings = SimpleNamespace(memgraph_uri="bolt://127.0.0.1:7687", memgraph_user="", memgraph_password="")
    tool = create_graph_search_tool(settings)

    context = await tool.ainvoke({"query": "приёмка лекарственных препаратов"})
    assert "Бизнес-процессы и шаги алгоритмов" in context
    assert "Приёмка лекарственных препаратов" in context
    assert "Входной документарный контроль" in context
    assert "Фармацевт-контролёр" in context or "Кладовщик" in context


@pytest.mark.asyncio
async def test_graph_search_storage_conditions():
    settings = SimpleNamespace(memgraph_uri="bolt://127.0.0.1:7687", memgraph_user="", memgraph_password="")
    tool = create_graph_search_tool(settings)

    context = await tool.ainvoke({"query": "Гам-КОВИД-Вак режим хранения"})
    assert "Режимы хранения" in context
    assert "Холодовая цепь 2–8 °C" in context
    assert "Вакцина Гам-КОВИД-Вак" in context


@pytest.mark.asyncio
async def test_graph_search_systems_and_integrations():
    settings = SimpleNamespace(memgraph_uri="bolt://127.0.0.1:7687", memgraph_user="", memgraph_password="")
    tool = create_graph_search_tool(settings)

    context = await tool.ainvoke({"query": "WMS интеграции"})
    assert "Информационные системы и интеграции" in context
    assert "WMS" in context
    assert "1С:ERP Предприятие" in context or "МДЛП" in context


@pytest.mark.asyncio
async def test_graph_search_regulations():
    settings = SimpleNamespace(memgraph_uri="bolt://127.0.0.1:7687", memgraph_user="", memgraph_password="")
    tool = create_graph_search_tool(settings)

    context = await tool.ainvoke({"query": "GDP требования"})
    assert "Нормативные требования" in context
    assert "GDP" in context
    assert "дистрибьюторской практики" in context
