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

from assistent.application.agent_policy import (
    AgentRuntimeContext,
    AgentState,
    ctx,
    history_for_turn,
    last_question,
    finalize_response,
)
from assistent.application.answer_sources import strip_thinking
from assistent.observability.diagnostics import record
from assistent.api.security import has_role_access
from assistent.infrastructure.tools.postgres_tool import DATABASE_ROLES
from assistent.prompts import (
    INSPECT_TABLES_PROMPT,
    GENERATE_SQL_PROMPT,
    FIX_SQL_PROMPT,
    FORMAT_ANSWER_PROMPT,
    OPTIMIZE_DB_PROMPT,
    DIAGNOSE_DB_ERROR_PROMPT,
    DATABASE_ANALYSIS_SYSTEM,
)

logger = logging.getLogger(__name__)



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


OPTIMIZATION_REQUEST = re.compile(r"оптимиз\w*|ускор\w*|план\s+запрос\w*|\bexplain\b|медленн", re.I)
DATABASE_LOG_ERROR = re.compile(
    r"\bsqlstate\b|\bpsycopg\b|\bundefined_?(?:column|table)\b|"
    r"\b(?:column|relation|table)\b[^\n]{0,100}\bdoes not exist\b|lock\s+timeout",
    re.I,
)
QUERY_TABLE = re.compile(r"\b(?:from|join)\s+([a-z_][a-z_0-9]*(?:\.[a-z_][a-z_0-9]*)?)", re.I)


def is_database_diagnostic(question: str) -> bool:
    from assistent.application.database_agent import is_database_request

    return bool(DATABASE_LOG_ERROR.search(question)
                or OPTIMIZATION_REQUEST.search(question) and is_database_request(question))


def postgres_error(result: str) -> bool:
    return not result.strip() or result.lstrip().lower().startswith(
        ("ошибка", "error:", "сервис postgresql недоступен", "доступ к postgresql", "инструмент postgresql")
    )


def build_database_analyst_graph(
    llm: BaseChatModel,
    tools: Sequence[BaseTool] | None = None,
    checkpointer: BaseCheckpointSaver | Literal[False] | None = None,
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
        question = last_question(history)
        user_context = ctx(runtime).get("user_context", {})

        if not db_tool:
            return {
                **boundary,
                "question": question,
                "is_direct_command": True,
                "direct_command_result": "Инструмент PostgreSQL не подключен или выключен.",
            }

        # Role-based access control guard
        if not has_role_access(user_context, DATABASE_ROLES):
            return {
                **boundary,
                "question": question,
                "is_direct_command": True,
                "direct_command_result": "Отказ в доступе: Доступ к инструменту /db и подключённой базе данных разрешён только пользователям с ролями programmer и admin.",
            }

        # Check for direct /db commands and destructive operations
        clean_question = question.strip().strip("«»\"'").strip()
        if re.search(r'(?:покажи|перечисли|список|какие).{0,50}схем', clean_question, re.I):
            clean_question = '/db schemas'
        parts = clean_question.split(maxsplit=1)
        if parts and parts[0].lower() == "/db":
            subcmd = parts[1].strip() if len(parts) > 1 else ""
            destructive_pattern = re.compile(
                r"\b(DROP|DELETE|TRUNCATE|ALTER|UPDATE|INSERT|GRANT|REVOKE)\b",
                re.IGNORECASE,
            )
            if destructive_pattern.search(subcmd):
                return {
                    **boundary,
                    "question": question,
                    "is_direct_command": True,
                    "direct_command_result": "Ошибка безопасности: База данных работает строго в режиме Read-Only. Команды модификации данных и структуры (DROP, DELETE, UPDATE, ALTER, TRUNCATE, INSERT) категорически запрещены.",
                }
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

        is_opt = bool(OPTIMIZATION_REQUEST.search(question))
        is_err = bool(DATABASE_LOG_ERROR.search(question))
        extracted_sql = None
        selected_tables: list[str] = []
        if is_opt or is_err:
            sql_match = re.search(r"```(?:sql)?\s*(SELECT\b[\s\S]*?)\s*```"
                                  r"|(?:\bSELECT\s+[^;`]*?\bFROM\s+[^;`]+(?:;|$))", question, re.I)
            if sql_match:
                extracted_sql = clean_sql(sql_match.group(1) or sql_match.group(0))
                selected_tables = list(dict.fromkeys(name.lower() for name in QUERY_TABLE.findall(extracted_sql)))

        try:
            tables_res = "" if selected_tables else await db_tool.ainvoke(
                {"operation": "tables", "schema_name": "public"},
                config={"configurable": {"user_context": user_context}},
            )
            tables_text = str(tables_res)
        except Exception as e:
            logger.warning("Failed to fetch tables list: %s", e)
            tables_text = ""

        # Step B: Select relevant table(s) (explicitly named in question or picked by LLM)
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
                header = f"Таблица {schema_name}.{table_name}:"
                clean_desc = str(desc).strip()
                if clean_desc.startswith(header):
                    schema_descriptions.append(clean_desc)
                else:
                    schema_descriptions.append(f"{header}\n{clean_desc}")
            except Exception as e:
                logger.warning("Failed to describe table %s: %s", t, e)

        schema_info = "\n\n".join(schema_descriptions) if schema_descriptions else tables_text
        record("db_inspect_schema", tables=selected_tables, length=len(schema_info), schema=schema_info[:2000])

        # Step D: Specialized handling for Optimization requests or Log Error diagnosis
        if is_err and extracted_sql:
            record("db_diagnose_error", tables=selected_tables, error=question[:500])
            if not schema_descriptions or all(postgres_error(item.split("\n", 1)[1]) for item in schema_descriptions):
                return {
                    **boundary, "question": question, "is_direct_command": True,
                    "direct_command_result": f"Не удалось проверить схему для исправления SQL.\n{schema_info}",
                }
            repair = await fix_sql_node({
                "question": question, "sql_query": extracted_sql,
                "explain_error": "Используй сообщение об ошибке из лога в запросе пользователя.",
                "schema_info": schema_info,
            }, runtime)
            corrected_sql = repair["sql_query"]
            if corrected_sql == extracted_sql or not re.match(r"SELECT\b", corrected_sql, re.I):
                ans = "Не удалось подготовить исправленный SELECT по доступной схеме."
            else:
                verification = await execute_sql_node({"sql_query": corrected_sql}, runtime)
                result = verification["query_result"]
                outcome = "Проверка завершилась ошибкой" if postgres_error(result) else "Результат проверки PostgreSQL"
                reported_error = next((line.strip()[:300] for line in question.splitlines()
                                       if DATABASE_LOG_ERROR.search(line)), "")
                ans = (f"Ошибка из лога: {reported_error}\n\n"
                       f"Исправленный запрос по схеме:\n```sql\n{corrected_sql}\n```\n\n{outcome}:\n{result}")
            return {**boundary, "question": question, "is_direct_command": True, "direct_command_result": ans}

        if is_opt and (schema_info or extracted_sql):
            explain_plan = "План запроса не указан пользователем"
            if extracted_sql:
                try:
                    exp_res = await db_tool.ainvoke(
                        {"operation": "explain", "sql": extracted_sql},
                        config={"configurable": {"user_context": user_context}},
                    )
                    explain_plan = str(exp_res)
                except Exception:
                    explain_plan = "Ошибка получения плана PostgreSQL."

            record("db_optimize_query", sql=extracted_sql, plan=explain_plan[:2000], tables=selected_tables)

            if postgres_error(explain_plan):
                ans = (f"Не удалось получить план EXPLAIN. {explain_plan}\n\n"
                       f"Результат проверки схемы:\n{schema_info or 'Схема недоступна.'}\n\n"
                       "Оценить эффект оптимизации по плану пока нельзя.")
                return {**boundary, "question": question, "is_direct_command": True, "direct_command_result": ans}

            opt_res = await llm.ainvoke([
                SystemMessage(content=DATABASE_ANALYSIS_SYSTEM),
                HumanMessage(content=OPTIMIZE_DB_PROMPT.format(
                    question=question,
                    schema_info=schema_info,
                    explain_plan=explain_plan,
                )),
            ])
            ans = strip_thinking(str(opt_res.content)).strip()
            if extracted_sql:
                ans = f"План PostgreSQL:\n```text\n{explain_plan}\n```\n\n{ans}".rstrip()
            return {
                **boundary,
                "question": question,
                "is_direct_command": True,
                "direct_command_result": ans,
            }

        if is_err and schema_info:
            record("db_diagnose_error", tables=selected_tables, error=question[:500])
            err_res = await llm.ainvoke([
                SystemMessage(content=DATABASE_ANALYSIS_SYSTEM),
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
                SystemMessage(content=DATABASE_ANALYSIS_SYSTEM),
                HumanMessage(content=prompt),
            ])
            query = clean_sql(str(res.content))
        except Exception as exc:
            logger.error("SQL generation failed: %s", exc)
            query = ""

        record("db_generate_sql", sql=query, tables=state.get("target_tables", []))
        return {"sql_query": query}

    # 3. Explain Check Node
    async def explain_check_node(state: DatabaseAnalystState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        sql_query = state.get("sql_query", "").strip()
        user_context = ctx(runtime).get("user_context", {})

        if not sql_query or not db_tool:
            record("db_explain_check", valid=False, error="Пустой или отсутствующий SQL-запрос", sql=sql_query)
            return {"explain_valid": False, "explain_error": "Пустой или отсутствующий SQL-запрос"}

        destructive_pattern = re.compile(
            r"\b(DROP|DELETE|TRUNCATE|ALTER|UPDATE|INSERT|GRANT|REVOKE)\b",
            re.IGNORECASE,
        )
        if destructive_pattern.search(sql_query):
            record("db_explain_check", valid=False, error="Запрещены DDL/DML операции", sql=sql_query)
            return {
                "explain_valid": False,
                "explain_error": "Ошибка безопасности: База данных работает строго в режиме Read-Only. Запрещены любые модифицирующие операции (DROP, DELETE, UPDATE, ALTER, TRUNCATE, INSERT).",
            }

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
                record("db_explain_check", valid=False, error=explain_str[:2000], sql=sql_query)
                return {"explain_valid": False, "explain_error": explain_str}

            record("db_explain_check", valid=True, plan=explain_str[:2000], sql=sql_query)
            return {"explain_valid": True, "explain_plan": explain_str, "explain_error": None}
        except Exception as exc:
            record("db_explain_check", valid=False, error=str(exc)[:1000], sql=sql_query)
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
                SystemMessage(content=DATABASE_ANALYSIS_SYSTEM),
                HumanMessage(content=prompt),
            ])
            fixed_sql = clean_sql(str(res.content))
        except Exception as exc:
            logger.error("Fix SQL failed: %s", exc)
            fixed_sql = sql_query

        record("db_fix_sql", attempt=attempts, old_sql=sql_query, fixed_sql=fixed_sql, error=explain_error[:1000] if explain_error else None)
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
            logger.warning("PostgreSQL verification failed error_type=%s", type(exc).__name__)
            res_str = "Ошибка при выполнении запроса PostgreSQL."

        record("db_execute_sql", sql=sql_query, length=len(res_str), result=res_str[:2000])
        return {"query_result": res_str}

    # 6. Format Answer Node
    async def format_answer_node(state: DatabaseAnalystState, runtime: Runtime[AgentRuntimeContext]) -> dict[str, Any]:
        if state.get("is_direct_command"):
            ans = state.get("direct_command_result", "")
            record("db_format_answer", length=len(ans), answer=ans[:2000])
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
            record("db_format_answer", length=len(ans), answer=ans[:2000])
            return {"messages": [finalize_response(AIMessage(content=ans), sources=[], active="database")]}

        prompt = FORMAT_ANSWER_PROMPT.format(
            question=question,
            sql_query=sql_query,
            query_result=query_result[:6000],
        )
        try:
            res = await llm.ainvoke([
                SystemMessage(content=DATABASE_ANALYSIS_SYSTEM),
                HumanMessage(content=prompt),
            ])
            ans = strip_thinking(str(res.content)).strip()
        except Exception as exc:
            ans = f"Результат выполнения SQL ({sql_query}):\n\n{query_result}"

        record("db_format_answer", length=len(ans), answer=ans[:2000])
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
