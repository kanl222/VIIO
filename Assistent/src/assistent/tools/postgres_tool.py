"""Role-checked adapter to the restricted Postgres MCP Pro server."""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from assistent.tools.mcp_adapter import call_read_tool

from assistent.config import Settings
from assistent.security import has_role_access, require_roles

logger = logging.getLogger(__name__)
DATABASE_ROLES = frozenset({"programmer", "admin"})


def can_access_database(context: dict | None) -> bool:
    return has_role_access(context, DATABASE_ROLES)


class PostgresMCPClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def call(self, name: str, arguments: dict) -> str:
        # Fixed allowlist: tuning, write tools and arbitrary MCP calls are not exposed.
        if name not in {"list_schemas", "list_objects", "get_object_details", "execute_sql", "explain_query"}:
            raise ValueError("Unsupported PostgreSQL operation")
        if name == "explain_query" and (arguments.get("analyze") is not False or arguments.get("hypothetical_indexes")):
            raise ValueError("Only EXPLAIN without execution or hypothetical indexes is allowed")
        text, failed = await call_read_tool(
            self.settings.pgsql_mcp_url, name, arguments,
            allowed={"list_schemas", "list_objects", "get_object_details", "execute_sql", "explain_query"},
            timeout=self.settings.pgsql_timeout_seconds, sse=True)
        # Upstream sometimes returns errors as TextContent without isError.
        if failed or text.lstrip().lower().startswith("error:"):
            return "Ошибка PostgreSQL: запрос отклонён или недоступны таблица/подключение. Проверьте SQL и права пользователя БД."
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
                # Do not inject LIMIT into the original SQL: it changes the plan.
                # Wrapping as a derived table excludes DML/DDL and multiple statements.
                name, args = "explain_query", {"sql": f"SELECT * FROM ({query}) AS viio_explain", "analyze": False, "hypothetical_indexes": []}
            else:
                name, args = "execute_sql", {"sql": f"SELECT * FROM ({query}) AS viio_readonly_result LIMIT {settings.pgsql_max_rows}"}
        context = config.get("configurable", {}).get("user_context", {})
        logger.info("PostgreSQL read: user_id=%s operation=%s", context.get("user_id"), operation)
        try:
            return await backend.call(name, args)
        except Exception:
            # Never include connection strings or upstream exception text in chat/logs.
            return "Сервис PostgreSQL недоступен или превышено время ожидания. Проверьте подключение и настройки."

    postgresql_readonly.metadata = {'allowed_roles': sorted(DATABASE_ROLES)}
    return postgresql_readonly
