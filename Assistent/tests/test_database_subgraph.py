import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from assistent.config import Settings
from assistent.services.database_subgraph import (
    build_database_analyst_graph,
    clean_sql,
)
from assistent.services.supervisor_graph import build_supervisor_graph
from assistent.tools.postgres_tool import create_postgres_readonly_tool
from test_required_retrieval import NoToolCallsModel


def test_clean_sql_helper():
    assert clean_sql("SELECT 1;") == "SELECT 1"
    assert clean_sql("```sql\nSELECT * FROM users;\n```") == "SELECT * FROM users"
    assert clean_sql("<think>Thinking</think> SELECT count(*) FROM orders;") == "SELECT count(*) FROM orders"


@pytest.mark.asyncio
async def test_database_subgraph_direct_command():
    class MockClient:
        async def call(self, name: str, arguments: dict) -> str:
            if name == "list_schemas":
                return "public, qa, analytics"
            return "ok"

    settings = Settings(_env_file=None, pgsql_enabled=True)
    db_tool = create_postgres_readonly_tool(settings, client=MockClient())

    llm = NoToolCallsModel(messages=iter([]))
    graph = build_database_analyst_graph(
        llm=llm,
        tools=[db_tool],
        checkpointer=MemorySaver(),
    )

    res = await graph.ainvoke(
        {"messages": [HumanMessage(content="/db schemas")]},
        context={"user_context": {"user_id": "u1", "role": "programmer"}},
        config={"configurable": {"thread_id": "t-sql-1"}},
    )

    final_msg = res["messages"][-1]
    assert "public, qa, analytics" in final_msg.content


@pytest.mark.asyncio
async def test_database_subgraph_text_to_sql_happy_path():
    class MockClient:
        async def call(self, name: str, arguments: dict) -> str:
            if name == "list_objects":
                return "orders, users, products"
            if name == "get_object_details":
                return "Column: id (int), status (varchar), amount (numeric)"
            if name == "explain_query":
                return "Aggregate (cost=10.5..10.6 rows=1 width=8)"
            if name == "execute_sql":
                return '[{"count": 42}]'
            return "ok"

    settings = Settings(_env_file=None, pgsql_enabled=True)
    db_tool = create_postgres_readonly_tool(settings, client=MockClient())

    # Mock LLM sequence:
    # 1. pick tables -> "public.orders"
    # 2. generate SQL -> "SELECT count(*) FROM public.orders WHERE status = 'completed'"
    # 3. format answer -> "Всего найдено 42 выполненных заказа."
    llm = NoToolCallsModel(messages=iter([
        AIMessage(content="public.orders"),
        AIMessage(content="SELECT count(*) FROM public.orders WHERE status = 'completed'"),
        AIMessage(content="Всего найдено 42 выполненных заказа.\n```sql\nSELECT count(*) FROM public.orders WHERE status = 'completed'\n```"),
    ]))

    graph = build_database_analyst_graph(
        llm=llm,
        tools=[db_tool],
        checkpointer=MemorySaver(),
    )

    res = await graph.ainvoke(
        {"messages": [HumanMessage(content="Сколько завершенных заказов в базе?")]},
        context={"user_context": {"user_id": "u1", "role": "programmer"}},
        config={"configurable": {"thread_id": "t-sql-2"}},
    )

    final_msg = res["messages"][-1]
    assert "42" in final_msg.content
    assert "SELECT count(*)" in final_msg.content


@pytest.mark.asyncio
async def test_database_subgraph_fix_loop_on_explain_error():
    explain_calls = 0

    class MockClient:
        async def call(self, name: str, arguments: dict) -> str:
            nonlocal explain_calls
            if name == "list_objects":
                return "orders, users"
            if name == "get_object_details":
                return "Column: id, amount"
            if name == "explain_query":
                explain_calls += 1
                if explain_calls == 1:
                    return 'ERROR: column "total_price" does not exist'
                return "Aggregate (cost=5.0..5.1 rows=1 width=8)"
            if name == "execute_sql":
                return '[{"total": 15000}]'
            return "ok"

    settings = Settings(_env_file=None, pgsql_enabled=True)
    db_tool = create_postgres_readonly_tool(settings, client=MockClient())

    # 1. pick tables -> "public.orders"
    # 2. generate initial SQL (erroneous) -> "SELECT sum(total_price) FROM public.orders"
    # 3. fix SQL -> "SELECT sum(amount) FROM public.orders"
    # 4. format answer -> "Общая сумма: 15000"
    llm = NoToolCallsModel(messages=iter([
        AIMessage(content="public.orders"),
        AIMessage(content="SELECT sum(total_price) FROM public.orders"),
        AIMessage(content="SELECT sum(amount) FROM public.orders"),
        AIMessage(content="Общая сумма заказов составляет 15 000 рублей."),
    ]))

    graph = build_database_analyst_graph(
        llm=llm,
        tools=[db_tool],
        checkpointer=MemorySaver(),
    )

    res = await graph.ainvoke(
        {"messages": [HumanMessage(content="Какая сумма всех заказов?")]},
        context={"user_context": {"user_id": "u1", "role": "programmer"}},
        config={"configurable": {"thread_id": "t-sql-3"}},
    )

    assert explain_calls == 2
    final_msg = res["messages"][-1]
    assert "15 000" in final_msg.content


@pytest.mark.asyncio
async def test_supervisor_with_database_analyst():
    class MockClient:
        async def call(self, name: str, arguments: dict) -> str:
            if name == "list_objects":
                return "users"
            if name == "get_object_details":
                return "Column: id, username, is_active"
            if name == "explain_query":
                return "Seq Scan on users (cost=0.0..1.0 rows=5 width=4)"
            if name == "execute_sql":
                return '[{"count": 5}]'
            return "ok"

    settings = Settings(_env_file=None, pgsql_enabled=True)
    db_tool = create_postgres_readonly_tool(settings, client=MockClient())

    llm = NoToolCallsModel(messages=iter([
        AIMessage(content="public.users"),
        AIMessage(content="SELECT count(*) FROM public.users"),
        AIMessage(content="В системе зарегистрировано 5 активных пользователей."),
    ]))

    supervisor = build_supervisor_graph(
        llm=llm,
        tools=[db_tool],
        database_analyst=True,
    )

    res = await supervisor.ainvoke(
        {"messages": [HumanMessage(content="/db Сколько пользователей в таблице users?")]},
        context={"user_context": {"user_id": "p1", "role": "programmer"}},
        config={"configurable": {"thread_id": "t-sup-sql"}},
    )

    final_msg = res["messages"][-1]
    assert "5" in final_msg.content
