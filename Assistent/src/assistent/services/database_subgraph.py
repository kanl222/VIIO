"""Interactive Text-to-SQL Database Analyst StateGraph.

Pipeline:
InspectSchema -> GenerateSQL -> ExplainCheck -> [ExecuteSQL | FixSQL]
FixSQL -> ExplainCheck (up to max_fix_attempts)
ExecuteSQL -> FormatAnswer -> END
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from assistent.prompts import DATABASE_SYSTEM_PROMPT
from assistent.services.agent_policy import (
    AgentRuntimeContext,
    AgentState,
    ctx,
    history_for_turn,
    last_question,
    finalize_response,
)
from assistent.services.answer_sources import clean_answer, strip_thinking
from assistent.services.diagnostics import record

logger = logging.getLogger(__name__)

INSPECT_TABLES_PROMPT = """Ты — аналитик баз данных PostgreSQL.
Пользователь задал вопрос о данных:
{question}

Список таблиц в базе данных:
{available_tables}

Выбери от 1 до 3 таблиц, необходимых для ответа на этот вопрос.
Напиши только имена таблиц через запятую, например: public.orders, public.users.
Если точная таблица неясна, напиши первую подходящую."""

GENERATE_SQL_PROMPT = """Ты — эксперт по SQL для PostgreSQL.
Вопрос пользователя:
{question}

Схема структуры таблиц:
{schema_info}

Правила:
1. Составь один безопасный SELECT-запрос для получения ответа.
2. Никаких изменений данных (DML/DDL: INSERT, UPDATE, DELETE, DROP запрещены).
3. Используй точные названия колонок и таблиц из схемы выше.
4. Применяй необходимые агрегаты (COUNT, SUM, AVG), условия WHERE, группировки GROUP BY и сортировки ORDER BY.
5. Выведи только SQL-запрос одной строкой или блоком БЕЗ кавычек Markdown и без лишнего текста."""

FIX_SQL_PROMPT = """Предыдущий SQL-запрос вызвал ошибку при проверке плана (EXPLAIN):
Запрос:
{sql_query}

Ошибка PostgreSQL:
{explain_error}

Схема таблиц:
{schema_info}

Вопрос пользователя:
{question}

Исправь SQL-запрос, чтобы он выполнился без ошибок. Выведи только исправленный SQL-запрос без Markdown."""

FORMAT_ANSWER_PROMPT = """Ты — ассистент аналитики данных PostgreSQL.
Вопрос пользователя:
{question}

Выполненный SQL-запрос:
{sql_query}

Результат из базы данных:
{query_result}

Сформулируй понятный ответ на русском языке:
- Ответь прямо на вопрос пользователя, используя полученные данные.
- Если есть строки данных, выведи их в виде аккуратной Markdown-таблицы.
- Если строк нет, прямо сообщи об этом.
- В конце укажи выполненный SQL-запрос в блоке ```sql ... ```."""

OPTIMIZE_DB_PROMPT = """Ты — эксперт по оптимизации баз данных PostgreSQL.
Пользователь просит оптимизировать запрос или структуру таблицы:
{question}

Схема структуры таблиц и текущие индексы:
{schema_info}

План выполнения запроса (EXPLAIN):
{explain_plan}

Сформулируй профессиональные рекомендации по оптимизации (в соответствии с postgres-patterns):
1. Разбери узлы плана EXPLAIN (Seq Scan, Hash Join, Nested Loop, Sort) и укажи конкретное место замедления.
2. Проверь наличие индексов по условиям фильтрации (WHERE) и объединения (JOIN).
3. Предложи конкретную DDL команду создания индекса (CREATE INDEX ...):
   - Колонки проверки равенства (=) первыми, колонки диапазона (>, <, BETWEEN) вторыми.
   - Используй B-tree для общих фильтров, BRIN для логов/таймстемпов, GIN для JSONB.
4. Если запрос можно переписать эффективнее — приведи исправленный SQL.
5. Объясни, какой ожидается выигрыш производительности (индексное сканирование Index Scan / Bitmap Index Scan вместо последовательного Seq Scan).
6. Ответь кратко, понятно и по делу на русском языке."""

DIAGNOSE_DB_ERROR_PROMPT = """Ты — эксперт PostgreSQL по анализу и исправлению ошибок из логов.
Пользователь сообщил об ошибке из логов:
{question}

Реальная структура таблиц и колонок в базе данных:
{schema_info}

Задача:
1. Определи причину ошибки (например: опечатка в имени колонки, обращение к несуществующему полю, несовместимость типов данных, блокировка строк).
2. Сравни ошибочное название с реальными колонками из схемы выше и четко назови правильное имя столбца/таблицы.
3. Предоставь исправленный рабочий SQL-запрос в блоке ```sql ... ```.
4. Дай краткое и понятное объяснение ошибки и способа её предотвращения на русском языке."""


class DatabaseAnalystState(AgentState, total=False):
    question: str
    is_direct_command: bool
    direct_command_result: str
    target_tables: list[str]
    schema_info: str
    sql_query: str
    explain_plan: str
    explain_valid: bool
    explain_error: str | None
    fix_attempts: int
    query_result: str
    formatted_answer: str


def clean_sql(text: str) -> str:
    """Strips thinking blocks, markdown fences, and trailing semicolons from SQL."""
    prose = strip_thinking(text).strip()
    match = re.search(r"```(?:sql)?\s*([\s\S]*?)\s*```", prose, re.I)
    if match:
        prose = match.group(1).strip()
    return prose.rstrip(";").strip()


def build_database_analyst_graph(
    llm: BaseChatModel,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    system_prompt: str | None = None,
    max_fix_attempts: int = 2,
    **extra_kwargs: Any,
) -> CompiledStateGraph:
    """Builds an interactive Text-to-SQL analyst graph with schema inspection, EXPLAIN verification, and auto-fix loop."""
    active_tools = list(tools or [])
    db_tool = next((t for t in active_tools if t.name == "postgresql_readonly"), None)

    # 1. Inspect Schema Node
    async def inspect_schema_node(state: DatabaseAnalystState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        history, boundary = history_for_turn(state, runtime)
        question = state.get("question") or last_question(history)
        user_context = ctx(runtime).get("user_context", {})

        if not db_tool:
            return {
                **boundary,
                "question": question,
                "is_direct_command": True,
                "direct_command_result": "Инструмент PostgreSQL не подключен или выключен.",
            }

        # Check for direct /db commands
        parts = question.strip().split(maxsplit=1)
        if parts and parts[0].lower() == "/db":
            subcmd = parts[1].strip() if len(parts) > 1 else ""
            subparts = subcmd.split(maxsplit=1)
            op = subparts[0].lower() if subparts else ""
            rest = subparts[1].strip() if len(subparts) > 1 else ""

            if op in ("schemas", "tables", "describe"):
                args: dict[str, Any] = {"operation": op}
                if op == "tables":
                    args["schema_name"] = rest or "public"
                elif op == "describe":
                    schema, _, table = rest.rpartition(".")
                    args["schema_name"] = schema or "public"
                    args["table"] = table or rest
                try:
                    res = await db_tool.ainvoke(args, config={"configurable": {"user_context": user_context}})
                    return {
                        **boundary,
                        "question": question,
                        "is_direct_command": True,
                        "direct_command_result": str(res),
                    }
                except Exception as exc:
                    return {
                        **boundary,
                        "question": question,
                        "is_direct_command": True,
                        "direct_command_result": f"Ошибка выполнения команды БД: {exc}",
                    }
            elif op in ("explain", "query") or (op.startswith("select")):
                sql_to_run = rest if op in ("explain", "query") else subcmd
                op_to_use = op if op in ("explain", "query") else "query"
                try:
                    res = await db_tool.ainvoke(
                        {"operation": op_to_use, "sql": sql_to_run},
                        config={"configurable": {"user_context": user_context}},
                    )
                    return {
                        **boundary,
                        "question": question,
                        "is_direct_command": True,
                        "direct_command_result": str(res),
                    }
                except Exception as exc:
                    return {
                        **boundary,
                        "question": question,
                        "is_direct_command": True,
                        "direct_command_result": f"Ошибка выполнения {op_to_use}: {exc}",
                    }

        # Step A: Get list of tables in public
        try:
            tables_res = await db_tool.ainvoke(
                {"operation": "tables", "schema_name": "public"},
                config={"configurable": {"user_context": user_context}},
            )
            tables_text = str(tables_res)
        except Exception as e:
            logger.warning("Failed to fetch tables list: %s", e)
            tables_text = ""

        # Step B: Select relevant table(s) (explicitly named in question or picked by LLM)
        selected_tables: list[str] = []
        if tables_text:
            for t_candidate in re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]+\b", question):
                if t_candidate in tables_text and t_candidate not in selected_tables:
                    selected_tables.append(t_candidate)

        if not selected_tables and tables_text and "ошибка" not in tables_text.lower():
            try:
                pick_res = await llm.ainvoke([
                    HumanMessage(content=INSPECT_TABLES_PROMPT.format(question=question, available_tables=tables_text[:3000]))
                ])
                raw_picked = strip_thinking(str(pick_res.content)).strip()
                selected_tables = [t.strip().strip("'\"") for t in raw_picked.split(",") if t.strip()]
            except Exception as e:
                logger.warning("Failed to pick tables with LLM: %s", e)

        # Step C: Describe chosen tables
        schema_descriptions = []
        for t in selected_tables[:2]:
            schema_name, _, table_name = t.rpartition(".")
            schema_name = schema_name or "public"
            table_name = table_name or t
            try:
                desc = await db_tool.ainvoke(
                    {"operation": "describe", "schema_name": schema_name, "table": table_name},
                    config={"configurable": {"user_context": user_context}},
                )
                schema_descriptions.append(f"Таблица {schema_name}.{table_name}:\n{desc}")
            except Exception as e:
                logger.warning("Failed to describe table %s: %s", t, e)

        schema_info = "\n\n".join(schema_descriptions) if schema_descriptions else tables_text
        record("db_inspect_schema", tables=selected_tables, length=len(schema_info))

        # Step D: Specialized handling for Optimization requests or Log Error diagnosis
        is_opt = bool(re.search(r"оптимиз\w*|ускор\w*|план\s+запрос\w*|explain|индекс\w*|медленн", question, re.I))
        is_err = bool(re.search(r"ошибк\w*|psycopg|traceback|undefined_column|does\s+not\s+exist|lock\s+timeout|не\s+существует", question, re.I))

        if is_opt and schema_info:
            sql_match = re.search(r"```(?:sql)?\s*([\s\S]*?)\s*```|(?:SELECT\s+[\s\S]+?(?:;|$))", question, re.I)
            explain_plan = "План запроса не указан пользователем"
            if sql_match:
                extracted_sql = clean_sql(sql_match.group(1) if sql_match.group(1) else sql_match.group(0))
                try:
                    exp_res = await db_tool.ainvoke(
                        {"operation": "explain", "sql": extracted_sql},
                        config={"configurable": {"user_context": user_context}},
                    )
                    explain_plan = str(exp_res)
                except Exception as exc:
                    explain_plan = f"Ошибка получения плана: {exc}"

            opt_res = await llm.ainvoke([
                SystemMessage(content=DATABASE_SYSTEM_PROMPT),
                HumanMessage(content=OPTIMIZE_DB_PROMPT.format(
                    question=question,
                    schema_info=schema_info,
                    explain_plan=explain_plan,
                )),
            ])
            ans = strip_thinking(str(opt_res.content)).strip()
            return {
                **boundary,
                "question": question,
                "is_direct_command": True,
                "direct_command_result": ans,
            }

        if is_err and schema_info:
            err_res = await llm.ainvoke([
                SystemMessage(content=DATABASE_SYSTEM_PROMPT),
                HumanMessage(content=DIAGNOSE_DB_ERROR_PROMPT.format(
                    question=question,
                    schema_info=schema_info,
                )),
            ])
            ans = strip_thinking(str(err_res.content)).strip()
            return {
                **boundary,
                "question": question,
                "is_direct_command": True,
                "direct_command_result": ans,
            }

        return {
            **boundary,
            "question": question,
            "is_direct_command": False,
            "target_tables": selected_tables,
            "schema_info": schema_info,
            "fix_attempts": 0,
        }

    def inspect_condition(state: DatabaseAnalystState, runtime: Runtime[AgentRuntimeContext]) -> Literal["format_answer", "generate_sql"]:
        if state.get("is_direct_command"):
            return "format_answer"
        return "generate_sql"

    # 2. Generate SQL Node
    async def generate_sql_node(state: DatabaseAnalystState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        question = state.get("question", "")
        schema_info = state.get("schema_info", "")

        prompt = GENERATE_SQL_PROMPT.format(question=question, schema_info=schema_info)
        try:
            res = await llm.ainvoke([
                SystemMessage(content=DATABASE_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            query = clean_sql(str(res.content))
        except Exception as exc:
            logger.error("SQL generation failed: %s", exc)
            query = ""

        record("db_generate_sql", query=query)
        return {"sql_query": query}

    # 3. Explain Check Node
    async def explain_check_node(state: DatabaseAnalystState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        sql_query = state.get("sql_query", "").strip()
        user_context = ctx(runtime).get("user_context", {})

        if not sql_query or not db_tool:
            return {"explain_valid": False, "explain_error": "Пустой или отсутствующий SQL-запрос"}

        try:
            explain_res = await db_tool.ainvoke(
                {"operation": "explain", "sql": sql_query},
                config={"configurable": {"user_context": user_context}},
            )
            explain_str = str(explain_res).strip()
            is_err = (
                "ошибка postgresql" in explain_str.lower()
                or "error:" in explain_str.lower()
                or "недоступ" in explain_str.lower()
                or "отклон" in explain_str.lower()
            )
            if is_err:
                record("db_explain_check", valid=False, error=explain_str)
                return {"explain_valid": False, "explain_error": explain_str}

            record("db_explain_check", valid=True, plan_snippet=explain_str[:200])
            return {"explain_valid": True, "explain_plan": explain_str, "explain_error": None}
        except Exception as exc:
            record("db_explain_check", valid=False, error=str(exc))
            return {"explain_valid": False, "explain_error": str(exc)}

    def explain_condition(state: DatabaseAnalystState, runtime: Runtime[AgentRuntimeContext]) -> Literal["execute_sql", "fix_sql", "format_answer"]:
        if state.get("explain_valid"):
            return "execute_sql"
        if state.get("fix_attempts", 0) < max_fix_attempts:
            return "fix_sql"
        return "format_answer"

    # 4. Fix SQL Node
    async def fix_sql_node(state: DatabaseAnalystState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        question = state.get("question", "")
        sql_query = state.get("sql_query", "")
        explain_error = state.get("explain_error", "")
        schema_info = state.get("schema_info", "")
        attempts = state.get("fix_attempts", 0) + 1

        prompt = FIX_SQL_PROMPT.format(
            question=question,
            sql_query=sql_query,
            explain_error=explain_error,
            schema_info=schema_info,
        )
        try:
            res = await llm.ainvoke([
                SystemMessage(content=DATABASE_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            fixed_sql = clean_sql(str(res.content))
        except Exception as exc:
            logger.error("Fix SQL failed: %s", exc)
            fixed_sql = sql_query

        record("db_fix_sql", attempt=attempts, old_sql=sql_query, fixed_sql=fixed_sql)
        return {"sql_query": fixed_sql, "fix_attempts": attempts}

    # 5. Execute SQL Node
    async def execute_sql_node(state: DatabaseAnalystState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        sql_query = state.get("sql_query", "")
        user_context = ctx(runtime).get("user_context", {})

        if not db_tool or not sql_query:
            return {"query_result": "Запрос не может быть выполнен."}

        try:
            query_res = await db_tool.ainvoke(
                {"operation": "query", "sql": sql_query},
                config={"configurable": {"user_context": user_context}},
            )
            res_str = str(query_res)
        except Exception as exc:
            res_str = f"Ошибка при выполнении запроса: {exc}"

        record("db_execute_sql", length=len(res_str))
        return {"query_result": res_str}

    # 6. Format Answer Node
    async def format_answer_node(state: DatabaseAnalystState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        if state.get("is_direct_command"):
            ans = state.get("direct_command_result", "")
            return {"messages": [finalize_response(AIMessage(content=ans), sources=[], active="database")]}

        question = state.get("question", "")
        sql_query = state.get("sql_query", "")
        query_result = state.get("query_result", "")
        explain_err = state.get("explain_error")

        if explain_err and not query_result:
            ans = (
                f"Не удалось построить корректный SQL-запрос для вопроса «{question}».\n"
                f"Ошибка проверки плана PostgreSQL:\n{explain_err}\n\n"
                f"Попробуйте уточнить формулировку или проверьте доступность таблиц."
            )
            return {"messages": [finalize_response(AIMessage(content=ans), sources=[], active="database")]}

        prompt = FORMAT_ANSWER_PROMPT.format(
            question=question,
            sql_query=sql_query,
            query_result=query_result[:6000],
        )
        try:
            res = await llm.ainvoke([
                SystemMessage(content=DATABASE_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            ans = strip_thinking(str(res.content)).strip()
        except Exception as exc:
            ans = f"Результат выполнения SQL ({sql_query}):\n\n{query_result}"

        record("db_format_answer", length=len(ans))
        return {"messages": [finalize_response(AIMessage(content=ans), sources=[], active="database")]}

    # Assembly
    builder = StateGraph(DatabaseAnalystState, context_schema=AgentRuntimeContext)
    builder.add_node("inspect_schema", inspect_schema_node)
    builder.add_node("generate_sql", generate_sql_node)
    builder.add_node("explain_check", explain_check_node)
    builder.add_node("fix_sql", fix_sql_node)
    builder.add_node("execute_sql", execute_sql_node)
    builder.add_node("format_answer", format_answer_node)

    builder.add_edge(START, "inspect_schema")
    builder.add_conditional_edges(
        "inspect_schema",
        inspect_condition,
        {
            "format_answer": "format_answer",
            "generate_sql": "generate_sql",
        },
    )
    builder.add_edge("generate_sql", "explain_check")
    builder.add_conditional_edges(
        "explain_check",
        explain_condition,
        {
            "execute_sql": "execute_sql",
            "fix_sql": "fix_sql",
            "format_answer": "format_answer",
        },
    )
    builder.add_edge("fix_sql", "explain_check")
    builder.add_edge("execute_sql", "format_answer")
    builder.add_edge("format_answer", END)

    return builder.compile(checkpointer=checkpointer)
