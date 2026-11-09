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

    # Таблица 'users' уже указана в вопросе, поэтому LLM вызывается для:
    # 1. generate SQL -> "SELECT count(*) FROM public.users"
    # 2. format answer -> "В системе зарегистрировано 5 активных пользователей."
    llm = NoToolCallsModel(messages=iter([
        AIMessage(content="SELECT count(*) FROM public.users"),
        AIMessage(content="В системе зарегистрировано 5 активных пользователей.\n```sql\nSELECT count(*) FROM public.users\n```"),
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


@pytest.mark.asyncio
async def test_database_optimization_workflow():
    """Проверяет оптимизацию БД: инспекция схемы, вызов EXPLAIN и рекомендация составного индекса."""
    called_operations = []

    class MockClient:
        async def call(self, name: str, arguments: dict) -> str:
            called_operations.append(name)
            if name == "list_objects":
                return "raw_pharmacy_transactions, orders"
            if name == "get_object_details":
                return "Columns: id (bigint), pharmacy_id (int), transaction_time (timestamptz), total_amount (numeric)\nIndexes: none"
            if name == "explain_query":
                return "Seq Scan on raw_pharmacy_transactions (cost=0.00..4520.00 rows=150 width=64)\nFilter: (pharmacy_id = 5 AND transaction_time > '2024-01-01')"
            return "ok"

    settings = Settings(_env_file=None, pgsql_enabled=True)
    db_tool = create_postgres_readonly_tool(settings, client=MockClient())

    llm = NoToolCallsModel(messages=iter([
        AIMessage(content=(
            "### Анализ плана запроса\n"
            "Запрос выполняет **Seq Scan** (последовательное сканирование всей таблицы) из-за отсутствия индексов.\n\n"
            "### Рекомендация по оптимизации (postgres-patterns)\n"
            "Создайте составной B-Tree индекс: сначала колонка равенства `pharmacy_id`, затем диапазон `transaction_time`:\n"
            "```sql\n"
            "CREATE INDEX idx_raw_pharmacy_tx_pharmacy_time ON public.raw_pharmacy_transactions (pharmacy_id, transaction_time);\n"
            "```\n"
            "Это заменит Seq Scan на **Index Scan** и ускорит выборку."
        )),
    ]))

    graph = build_database_analyst_graph(
        llm=llm,
        tools=[db_tool],
        checkpointer=MemorySaver(),
    )

    query = (
        "Оптимизируй медленный запрос к таблице raw_pharmacy_transactions в базе данных:\n"
        "```sql\n"
        "SELECT * FROM raw_pharmacy_transactions WHERE pharmacy_id = 5 AND transaction_time > '2024-01-01';\n"
        "```"
    )

    res = await graph.ainvoke(
        {"messages": [HumanMessage(content=query)]},
        context={"user_context": {"user_id": "p1", "role": "programmer"}},
        config={"configurable": {"thread_id": "t-opt-1"}},
    )

    assert "get_object_details" in called_operations
    assert "explain_query" in called_operations
    final_content = res["messages"][-1].content
    assert "Seq Scan" in final_content
    assert "CREATE INDEX" in final_content
    assert "idx_raw_pharmacy_tx_pharmacy_time" in final_content


@pytest.mark.asyncio
async def test_database_resolve_log_error():
    """Проверяет диагностику и исправление ошибки из логов (UndefinedColumn)."""
    called_operations = []

    class MockClient:
        async def call(self, name: str, arguments: dict) -> str:
            called_operations.append(name)
            if name == "list_objects":
                return "raw_pharmacy_transactions"
            if name == "get_object_details":
                return "Columns: id (bigint), pharmacy_id (int), transaction_time (timestamptz), total_amount (numeric)"
            return "ok"

    settings = Settings(_env_file=None, pgsql_enabled=True)
    db_tool = create_postgres_readonly_tool(settings, client=MockClient())

    llm = NoToolCallsModel(messages=iter([
        AIMessage(content=(
            "### Диагностика ошибки из логов\n"
            "**Причина:** в таблице `raw_pharmacy_transactions` нет колонки `transaction_date`.\n"
            "Реальное имя поля в схеме базы данных — **`transaction_time`**.\n\n"
            "### Исправленный SQL-запрос:\n"
            "```sql\n"
            "SELECT id, pharmacy_id, transaction_time, total_amount FROM raw_pharmacy_transactions WHERE pharmacy_id = 10;\n"
            "```"
        )),
    ]))

    graph = build_database_analyst_graph(
        llm=llm,
        tools=[db_tool],
        checkpointer=MemorySaver(),
    )

    log_error_msg = (
        "В логах вылезла ошибка: psycopg.errors.UndefinedColumn: column \"transaction_date\" does not exist "
        "at character 38 в таблице raw_pharmacy_transactions. Помоги исправить запрос."
    )

    res = await graph.ainvoke(
        {"messages": [HumanMessage(content=log_error_msg)]},
        context={"user_context": {"user_id": "p1", "role": "programmer"}},
        config={"configurable": {"thread_id": "t-log-err-1"}},
    )

    assert "get_object_details" in called_operations
    final_content = res["messages"][-1].content
    assert "transaction_time" in final_content
    assert "transaction_date" in final_content
    assert "```sql" in final_content


@pytest.mark.asyncio
async def test_supervisor_routes_db_optimization_and_log_error():
    """Проверяет, что супервизор корректно направляет запросы оптимизации и ошибок логов в Database Specialist."""
    from assistent.services.supervisor_graph import classify_route

    tools = {"postgresql_readonly", "github_repository"}
    user_context = {"user_id": "p1", "role": "programmer"}

    # 1. Запрос оптимизации БД
    q_opt = "Оптимизируй запрос к таблице raw_pharmacy_transactions в базе данных"
    assert classify_route(q_opt, tools, user_context) == "database"

    # 2. Ошибка БД из логов (psycopg)
    q_err = 'В логах ошибка: psycopg.errors.UndefinedColumn: column "transaction_date" does not exist в таблице raw_pharmacy_transactions'
    assert classify_route(q_err, tools, user_context) == "database"

    # 3. Вопрос о плане запроса EXPLAIN
    q_explain = "Проверь план explain для запроса SELECT count(*) FROM public.orders"
    assert classify_route(q_explain, tools, user_context) == "database"
