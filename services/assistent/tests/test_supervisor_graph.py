import pytest
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from assistent.services.supervisor_graph import build_supervisor_graph, classify_route
from assistent.tools.postgres_tool import create_postgres_readonly_tool
from assistent.tools.github_tool import create_github_tool
from assistent.config import Settings
from test_required_retrieval import NoToolCallsModel


def test_classify_route_logic():
    tools = {"search_knowledge_base", "github_repository", "postgresql_readonly"}
    prog_ctx = {"user_id": "u1", "role": "programmer"}
    empl_ctx = {"user_id": "u2", "role": "employee"}

    # Arithmetic & rewrite -> direct
    assert classify_route("2 + 2", tools, prog_ctx) == "direct"
    assert classify_route("Перепиши вежливо: Привет", tools, prog_ctx) == "direct"
    assert classify_route("Привет!", tools, empl_ctx) == "direct"

    # Database -> database (if authorized)
    assert classify_route("/db schemas", tools, prog_ctx) == "database"
    assert classify_route("Какие таблицы в postgres?", tools, prog_ctx) == "database"
    # If not authorized, falls back to domain/knowledge
    assert classify_route("Какие таблицы в postgres?", tools, empl_ctx) == "knowledge"

    # Coding & GitHub -> coding
    assert classify_route("/code Напиши функцию", tools, prog_ctx) == "coding"
    assert classify_route("Напиши скрипт на python", tools, prog_ctx) == "coding"
    assert classify_route("Какие репозитории доступны?", tools, prog_ctx) == "coding"
    assert classify_route("GitHub", tools, prog_ctx) == "coding"
    assert classify_route("какие репоизтории доступны", tools, prog_ctx) == "coding"

    # Knowledge
    assert classify_route("Как оформить отпуск в компании?", tools, empl_ctx) == "knowledge"


@pytest.mark.asyncio
async def test_supervisor_graph_execution():
    llm = NoToolCallsModel(messages=iter([AIMessage(content="Код готов"), AIMessage(content="Документ найден")]))
    saver = MemorySaver()
    settings = Settings(_env_file=None, github_mcp_enabled=True, github_repository="*", github_mcp_token="test", pgsql_enabled=True)
    github_tool = create_github_tool(settings)
    db_tool = create_postgres_readonly_tool(settings)
    
    supervisor = build_supervisor_graph(
        llm=llm,
        tools=[github_tool, db_tool],
        checkpointer=saver,
    )
    
    # 1. Direct arithmetic
    res1 = await supervisor.ainvoke(
        {"messages": [HumanMessage(content="2 + 2")]},
        context={"user_context": {"role": "programmer"}},
        config={"configurable": {"thread_id": "t1"}}
    )
    assert res1["messages"][-1].content == "4"

    # 2. Coding
    res2 = await supervisor.ainvoke(
        {"messages": [HumanMessage(content="/code Напиши def hello(): pass")]},
        context={"user_context": {"role": "programmer"}},
        config={"configurable": {"thread_id": "t2"}}
    )
    assert "Код готов" in res2["messages"][-1].content


@pytest.mark.asyncio
async def test_supervisor_database_execution():
    class MockPostgresClient:
        async def call(self, name: str, arguments: dict) -> str:
            return "Схемы: public, qa"

    settings = Settings(_env_file=None, pgsql_enabled=True)
    db_tool = create_postgres_readonly_tool(settings, client=MockPostgresClient())

    llm = NoToolCallsModel(messages=iter([AIMessage(content="Список схем: public, qa")]))
    supervisor = build_supervisor_graph(
        llm=llm,
        tools=[db_tool],
        checkpointer=MemorySaver(),
    )

    res = await supervisor.ainvoke(
        {"messages": [HumanMessage(content="/db schemas")]},
        context={"user_context": {"user_id": "u1", "role": "programmer"}},
        config={"configurable": {"thread_id": "t3"}},
    )
    assert "public, qa" in res["messages"][-1].content


@pytest.mark.asyncio
async def test_supervisor_unauthorized_database_fallback():
    # If employee sends /db schemas, classify_route routes to knowledge, not database
    llm = NoToolCallsModel(messages=iter([AIMessage(content="У вас нет доступа к базе данных.")]))
    supervisor = build_supervisor_graph(
        llm=llm,
        tools=[],
        checkpointer=MemorySaver(),
    )
    res = await supervisor.ainvoke(
        {"messages": [HumanMessage(content="/db schemas")]},
        context={"user_context": {"user_id": "u2", "role": "employee"}},
        config={"configurable": {"thread_id": "t4"}},
    )
    assert "нет доступа" in res["messages"][-1].content


@pytest.mark.asyncio
async def test_supervisor_review_node_provenance():
    # In review_node:
    # 1. attaches reference_context sources
    # 2. filters sources to only cited ones
    # 3. cleans internal tags
    llm = NoToolCallsModel(messages=iter([
        AIMessage(content="Согласно регламенту «Правила отпусков», заявление подается за 14 дней.")
    ]))
    supervisor = build_supervisor_graph(
        llm=llm,
        tools=[],
        checkpointer=MemorySaver(),
    )
    res = await supervisor.ainvoke(
        {"messages": [HumanMessage(content="Как подать на отпуск?")]},
        context={
            "user_context": {"role": "employee"},
            "reference_context": '<context source="Правила отпусков" doc_id="doc-123">Порядок подачи заявлений</context>',
        },
        config={"configurable": {"thread_id": "t5"}},
    )
    last_msg = res["messages"][-1]
    assert "Правила отпусков" in last_msg.content
    sources = last_msg.response_metadata.get("sources", [])
    assert len(sources) == 1
    assert sources[0]["title"] == "Правила отпусков"
    assert sources[0]["document_id"] == "doc-123"


def test_build_agent_graph_supervisor_flag():
    from assistent.services.graph_builder import build_agent_graph
    llm = NoToolCallsModel(messages=iter([]))
    graph = build_agent_graph(
        llm=llm,
        tools=[],
        supervisor=True,
    )
    assert graph is not None


@pytest.mark.asyncio
async def test_supervisor_routing_llm_with_thinking_and_markdown():
    # Routing LLM outputs label wrapped in thinking tags and markdown
    routing_model = NoToolCallsModel(messages=iter([
        AIMessage(content="<think>User wants a joke</think>\n**direct**"),
    ]))
    main_llm = NoToolCallsModel(messages=iter([
        AIMessage(content="Колобок повесился"),
    ]))
    supervisor = build_supervisor_graph(
        llm=main_llm,
        tools=[],
        checkpointer=MemorySaver(),
        routing_llm=routing_model,
    )
    # Question is not an arithmetic expression or greeting rule, so routing_llm is consulted
    res = await supervisor.ainvoke(
        {"messages": [HumanMessage(content="Расскажи короткий смешной анекдот")]},
        context={"user_context": {"role": "employee"}},
        config={"configurable": {"thread_id": "route-t1"}},
    )
    assert res["supervisor_route"] == "direct"
    assert "Колобок повесился" in res["messages"][-1].content


