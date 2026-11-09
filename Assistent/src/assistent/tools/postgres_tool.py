"""Role-checked adapter to the restricted Postgres MCP Pro server."""

from __future__ import annotations

import logging
import re
from typing import Annotated, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from assistent.tools.mcp_adapter import call_read_tool

from assistent.config import Settings
from assistent.security import has_role_access, require_roles

logger = logging.getLogger(__name__)
DATABASE_ROLES = frozenset({"programmer", "admin"})
SQLSTATE_ERRORS = {
    "42703": ("undefinedcolumn", "не найден столбец"),
    "42P01": ("undefinedtable", "не найдена таблица"),
    "42501": ("insufficientprivilege", "недостаточно прав"),
    "42601": ("syntaxerror", "синтаксическая ошибка"),
    "57014": ("querycanceled", "запрос отменён"),
}


def can_access_database(context: dict | None) -> bool:
    return has_role_access(context, DATABASE_ROLES)


class PostgresMCPClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def call(self, name: str, arguments: dict) -> str:
        # Фиксированный белый список: тюнинг, инструменты записи и произвольные MCP-вызовы не раскрываются.
        if name not in {"list_schemas", "list_objects", "get_object_details", "execute_sql", "explain_query"}:
            raise ValueError("Unsupported PostgreSQL operation")
        if name == "explain_query" and (arguments.get("analyze") is not False or arguments.get("hypothetical_indexes")):
            raise ValueError("Only EXPLAIN without execution or hypothetical indexes is allowed")
        text, failed = await call_read_tool(
            self.settings.pgsql_mcp_url, name, arguments,
            allowed={"list_schemas", "list_objects", "get_object_details", "execute_sql", "explain_query"},
            timeout=self.settings.pgsql_timeout_seconds, sse=True)
        # Вышестоящий сервис иногда возвращает ошибки как TextContent без флага isError.
        if failed or text.lstrip().lower().startswith("error:"):
            match = re.search(r"\bSQLSTATE[\s:=\[]+([0-9A-Z]{5})\b", text, re.I)
            code = match[1].upper() if match else next(
                (state for state, (kind, _) in SQLSTATE_ERRORS.items() if kind in text.lower()), None
            )
            logger.warning("PostgreSQL MCP rejected operation=%s sqlstate=%s", name, code or "unknown")
            if code:
                _, description = SQLSTATE_ERRORS.get(code, ("", "сервер отклонил операцию"))
                return f"Ошибка PostgreSQL (SQLSTATE {code}): {description}."
            return "Ошибка PostgreSQL: сервер отклонил операцию без кода причины. Подробности в журнале postgres-tool."
        limit = self.settings.pgsql_max_chars
        return text if len(text) <= limit else text[:limit] + "\n[Результат сокращён до лимита символов]"


def create_postgres_readonly_tool(settings: Settings, client: PostgresMCPClient | None = None):
    backend = client or PostgresMCPClient(settings)

    @tool("postgresql_readonly")
    @require_roles(DATABASE_ROLES, "Доступ к PostgreSQL разрешён только программистам и администраторам.")
    async def postgresql_readonly(
        operation: Literal["schemas", "tables", "describe", "query", "explain"],
        config: RunnableConfig,
        schema_name: Annotated[str, "Schema name only, e.g. analytics for analytics.orders. For tables and describe."] = "public",
        table: Annotated[str, "Table name only, e.g. orders for analytics.orders. Required for describe, separate from schema_name."] = "",
        sql: Annotated[str, "A single SELECT using actual qualified table and column names. Required for query and explain."] = "",
    ) -> str:
        """Read connected PostgreSQL. operation: schemas=list schemas; tables=list tables in schema_name;
        describe=columns/indexes for schema_name.table; query=execute SELECT sql, including COUNT/SUM;
        explain=plan sql without ANALYZE. No writes. Use query for actual counts, not describe.
        """
        if not settings.pgsql_enabled:
            return "Инструмент PostgreSQL выключен. Администратору нужно настроить PGSQL_* и включить PGSQL_ENABLED."
        if operation == "schemas":
            name, args = "list_schemas", {}
        elif operation == "tables":
            name, args = "list_objects", {"schema_name": schema_name, "object_type": "table"}
        elif operation == "describe":
            if not table.strip():
                return "Укажите имя таблицы для описания."
            name, args = "get_object_details", {"schema_name": schema_name, "object_name": table, "object_type": "table"}
        else:
            query = sql.strip().rstrip(";").strip()
            if not query or len(query) > 10000:
                return "Укажите один SELECT-запрос длиной до 10 000 символов."
            if operation == "explain":
                # Не внедряем LIMIT в исходный SQL: это меняет план выполнения.
                # Оборачивание в производную таблицу исключает DML/DDL и множественные инструкции.
                name, args = "explain_query", {"sql": f"SELECT * FROM ({query}) AS viio_explain", "analyze": False, "hypothetical_indexes": []}
            else:
                name, args = "execute_sql", {"sql": f"SELECT * FROM ({query}) AS viio_readonly_result LIMIT {settings.pgsql_max_rows}"}
        context = config.get("configurable", {}).get("user_context", {})
        logger.info("PostgreSQL read: user_id=%s operation=%s", context.get("user_id"), operation)
        try:
            return await backend.call(name, args)
        except Exception as error:
            # Никогда не включать строки подключения или текст исключений вышестоящего сервиса в чат/логи.
            logger.warning("PostgreSQL MCP operation=%s failed error_type=%s", name, type(error).__name__)
            return "Сервис PostgreSQL недоступен или превышено время ожидания. Проверьте подключение и настройки."

    postgresql_readonly.metadata = {'allowed_roles': sorted(DATABASE_ROLES)}
    return postgresql_readonly
