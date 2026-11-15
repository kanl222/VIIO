"""Скрипт имитации реальных сценариев работы агента:
1. Оптимизация БД (анализ схемы, EXPLAIN план, рекомендация составного индекса).
2. Диагностика и исправление ошибки БД из логов (UndefinedColumn).
3. Диагностика и исправление ошибки приложения из логов (Traceback / KeyError).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from unittest.mock import MagicMock

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from assistent.config import Settings
from assistent.services.database_subgraph import build_database_analyst_graph
from assistent.services.supervisor_graph import build_supervisor_graph
from assistent.tools.postgres_tool import create_postgres_readonly_tool
from assistent.services.coding_agent import CodingAgentService, MultiAgentService, CODING_SYSTEM_PROMPT
from assistent.services.graph_builder import build_agent_graph
from assistent.services.diagnostics import capture, record


def print_banner(title: str):
    print("\n" + "=" * 80)
    print(f"  {title.upper()}")
    print("=" * 80)


def print_step(step_name: str, details: str):
    print(f"\n[ШАГ] ---> {step_name}")
    print(details.strip())


async def run_scenario_1_db_optimization():
    print_banner("Сценарий 1: Оптимизация медленного запроса к БД (postgres-patterns)")

    user_query = (
        "Оптимизируй запрос к таблице raw_pharmacy_transactions в базе данных:\n"
        "```sql\n"
        "SELECT * FROM raw_pharmacy_transactions WHERE kassa_number = 'KASSA-01' AND transaction_date > '2024-01-01';\n"
        "```\n"
        "Сейчас запрос тормозит и делает Seq Scan."
    )

    print(f"Пользователь (программист):\n{user_query}\n")

    tool_log = []

    class RealisticPostgresClient:
        async def call(self, name: str, arguments: dict) -> str:
            tool_log.append((name, arguments))
            if name == "list_objects":
                return "raw_pharmacy_transactions, receipt_items_unindexed, daily_pharmacy_sales_denorm"
            if name == "get_object_details":
                return (
                    "Таблица public.raw_pharmacy_transactions:\n"
                    "  - tx_id: text\n"
                    "  - kassa_number: text\n"
                    "  - pharmacist_fio: text\n"
                    "  - transaction_date: text\n"
                    "  - total_amount: text\n"
                    "Индексы: отсутствуют (таблица неоптимизирована)"
                )
            if name == "explain_query":
                return (
                    "Seq Scan on raw_pharmacy_transactions  (cost=0.00..253.79 rows=7 width=352)\n"
                    "  Filter: ((transaction_date > '2024-01-01'::text) AND (kassa_number = 'KASSA-01'::text))"
                )
            return "ok"

    settings = Settings(_env_file=None, pgsql_enabled=True)
    db_tool = create_postgres_readonly_tool(settings, client=RealisticPostgresClient())

    optimization_advice = (
        "### Анализ плана запроса (EXPLAIN)\n"
        "1. **Текущее состояние:** Запрос выполняет **Seq Scan** (полное последовательное чтение 5000 строк таблицы) "
        "со стоимостью `cost=0.00..253.79` и последующей фильтрацией в памяти по условиям `kassa_number = 'KASSA-01'` "
        "и `transaction_date > '2024-01-01'`.\n"
        "2. **Причина:** В таблице `raw_pharmacy_transactions` отсутствуют индексы.\n\n"
        "### Рекомендация по оптимизации (postgres-patterns)\n"
        "Создайте составной B-Tree индекс по правилу: **колонки точного равенства первыми, диапазонные колонки вторыми**:\n\n"
        "```sql\n"
        "CREATE INDEX idx_raw_pharmacy_kassa_date\n"
        "  ON public.raw_pharmacy_transactions (kassa_number, transaction_date);\n"
        "```\n\n"
        "### Ожидаемый результат:\n"
        "- Замена `Seq Scan` на **`Bitmap Index Scan` / `Index Scan`**.\n"
        "- Снижение стоимости запроса с `253.79` до `< 5.00`.\n"
        "- Устранение I/O нагрузки на диск при росте таблицы."
    )

    llm = GenericFakeChatModel(messages=iter([AIMessage(content=optimization_advice)]))
    graph = build_database_analyst_graph(llm=llm, tools=[db_tool], checkpointer=MemorySaver())

    with capture('main', user_query, thread_id="sim-opt-1"):
        record("route", mode="database", supervisor=True, tools=["postgresql_readonly"], branch="database")
        record("specialist_dispatch", specialist="database", name="Аналитик базы данных (Database Analyst)", question=user_query[:200])
        res = await graph.ainvoke(
            {"messages": [HumanMessage(content=user_query)]},
            context={"user_context": {"user_id": "prog1", "role": "programmer"}},
            config={"configurable": {"thread_id": "sim-opt-1"}},
        )
        record("answer", reply=res["messages"][-1].content, sources=[])

    print_step("Вызовы инструментов PostgreSQL:", "")
    for op, args in tool_log:
        print(f"  * {op}: {args}")

    print_step("Ответ агента пользователю:", res["messages"][-1].content)


async def run_scenario_2_db_log_error():
    print_banner("Сценарий 2: Диагностика и исправление ошибки БД из логов (UndefinedColumn)")

    log_error_query = (
        "В логах вылезла ошибка:\n"
        "psycopg.errors.UndefinedColumn: column \"pharmacy_id\" does not exist at character 38 в таблице raw_pharmacy_transactions.\n"
        "Как исправить запрос:\n"
        "```sql\n"
        "SELECT id, pharmacy_id, transaction_date, total_amount FROM raw_pharmacy_transactions WHERE pharmacy_id = 10;\n"
        "```"
    )

    print(f"Пользователь (программист):\n{log_error_query}\n")

    tool_log = []

    class RealisticPostgresClient:
        async def call(self, name: str, arguments: dict) -> str:
            tool_log.append((name, arguments))
            if name == "list_objects":
                return "raw_pharmacy_transactions, receipt_items_unindexed, daily_pharmacy_sales_denorm"
            if name == "get_object_details":
                return (
                    "Таблица public.raw_pharmacy_transactions:\n"
                    "  - tx_id: text (PRIMARY KEY)\n"
                    "  - kassa_number: text\n"
                    "  - pharmacist_fio: text\n"
                    "  - client_card_num: text\n"
                    "  - transaction_date: text\n"
                    "  - total_amount: text"
                )
            return "ok"

    settings = Settings(_env_file=None, pgsql_enabled=True)
    db_tool = create_postgres_readonly_tool(settings, client=RealisticPostgresClient())

    error_fix_advice = (
        "### Диагностика ошибки из логов\n"
        "**Причина:** в таблице `public.raw_pharmacy_transactions` колонка `pharmacy_id` отсутствует.\n"
        "В текущей схеме идентификатором кассового узла аптеки выступает **`kassa_number`**, "
        "а первичным идентификатором транзакции — **`tx_id`** (вместо `id`).\n\n"
        "### Исправленный SQL-запрос:\n"
        "```sql\n"
        "SELECT tx_id, kassa_number, transaction_date, total_amount\n"
        "FROM public.raw_pharmacy_transactions\n"
        "WHERE kassa_number = 'KASSA-10';\n"
        "```\n\n"
        "### Рекомендация:\n"
        "Проверьте маппинг в ORM / репозитории, чтобы заменить устаревшее поле `pharmacy_id` на `kassa_number`."
    )

    llm = GenericFakeChatModel(messages=iter([AIMessage(content=error_fix_advice)]))
    graph = build_database_analyst_graph(llm=llm, tools=[db_tool], checkpointer=MemorySaver())

    with capture('main', log_error_query, thread_id="sim-err-2"):
        record("route", mode="database", supervisor=True, tools=["postgresql_readonly"], branch="database")
        record("specialist_dispatch", specialist="database", name="Аналитик базы данных (Database Analyst)", question=log_error_query[:200])
        res = await graph.ainvoke(
            {"messages": [HumanMessage(content=log_error_query)]},
            context={"user_context": {"user_id": "prog1", "role": "programmer"}},
            config={"configurable": {"thread_id": "sim-err-2"}},
        )
        record("answer", reply=res["messages"][-1].content, sources=[])

    print_step("Инспекция схемы БД агентом:", "")
    for op, args in tool_log:
        print(f"  * {op}: {args}")

    print_step("Ответ агента пользователю:", res["messages"][-1].content)


async def run_scenario_3_app_log_error():
    print_banner("Сценарий 3: Диагностика и исправление ошибки приложения из логов (KeyError)")

    app_error_query = (
        "Пользователь сообщил об ошибке из логов воркера ингестии документов:\n"
        "```text\n"
        "Traceback (most recent call last):\n"
        "  File '/app/Documents/src/viio_documents/service_profiles.py', line 124, in extract_chunks\n"
        "    chunk_idx = metadata['chunk_index']\n"
        "KeyError: 'chunk_index'\n"
        "```\n"
        "В чем причина и как исправить в коде?"
    )

    print(f"Пользователь:\n{app_error_query}\n")

    code_fix_advice = (
        "### Диагностика ошибки приложения\n"
        "Исключение `KeyError: 'chunk_index'` возникает из-за жесткого обращения по ключу `metadata['chunk_index']`.\n"
        "Если входящий документ не был предварительно разбит на фрагменты (например, для метаданных заголовка или "
        "пустых файлов), ключ `'chunk_index'` в словаре `metadata` отсутствует.\n\n"
        "### Исправление в `service_profiles.py`:\n"
        "Замените прямое обращение на безопасный метод `.get()` с дефолтным значением `0`:\n\n"
        "```python\n"
        "# Было:\n"
        "# chunk_idx = metadata['chunk_index']\n\n"
        "# Стало:\n"
        "chunk_idx = metadata.get('chunk_index', 0)\n"
        "```\n\n"
        "Либо с предварительной проверкой:\n"
        "```python\n"
        "chunk_idx = metadata.get('chunk_index')\n"
        "if chunk_idx is None:\n"
        "    logger.warning('Missing chunk_index in metadata, defaulting to 0')\n"
        "    chunk_idx = 0\n"
        "```"
    )

    @tool("knowledge_search")
    async def mock_knowledge_search(query: str) -> str:
        """Поиск по базе знаний и кодовой базе."""
        return (
            "<context source=\"service_profiles.py\" doc_id=\"doc-1\">\n"
            "def extract_chunks(metadata: dict) -> list:\n"
            "    chunk_idx = metadata['chunk_index']\n"
            "    return []\n"
            "</context>"
        )

    coder_graph = build_agent_graph(
        GenericFakeChatModel(messages=iter([AIMessage(content=code_fix_advice)])),
        tools=[],
        checkpointer=MemorySaver(),
        system_prompt=CODING_SYSTEM_PROMPT,
    )
    coder_service = CodingAgentService(coder_graph, ["programmer", "admin"], knowledge_search=mock_knowledge_search)
    general_service = MagicMock()
    multi_agent = MultiAgentService(general_service, coder_service)

    with capture('main', app_error_query, thread_id="sim-thread-3"):
        record("route", mode="coding", supervisor=True, tools=["knowledge_search"], branch="coding")
        record("specialist_dispatch", specialist="coding", name="Инженер-программист (Coding Specialist)", question=app_error_query[:200])
        reply, _ = await multi_agent.chat(app_error_query, "sim-thread-3", {"user_id": "prog1", "role": "programmer"})
        record("answer", reply=reply, sources=[])

    print_step("Маршрутизация:", "Запрос классифицирован как Coding/Debugging intent -> передан Coding Specialist")
    print_step("Ответ агента пользователю:", reply)


async def main():
    trace_dir = "D:/Source/VIIO/tmp/traces"
    os.environ["VIIO_TRACE_DIR"] = trace_dir
    os.makedirs(trace_dir, exist_ok=True)

    print("================================================================================")
    print("      ИМИТАЦИЯ ТРЕХ РЕАЛЬНЫХ СЦЕНАРИЕВ РАБОТЫ АГЕНТА VIIO")
    print("================================================================================")
    await run_scenario_1_db_optimization()
    await run_scenario_2_db_log_error()
    await run_scenario_3_app_log_error()
    print_banner("ВСЕ СЦЕНАРИИ УСПЕШНО ПРОЙДЕНЫ")

    # Синхронизация трасс с контейнером диагностики
    try:
        subprocess.run(["docker", "cp", f"{trace_dir}/.", "viio_assistent:/traces"], check=False)
        print("\n[ДИАГНОСТИКА] Трассы успешно загружены в веб-интерфейс диагностики: http://localhost:3010\n")
    except Exception as exc:
        print(f"\n[ДИАГНОСТИКА] Предупреждение: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
