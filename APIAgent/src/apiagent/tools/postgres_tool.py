"""Role-checked adapter to the restricted Postgres MCP Pro server."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from mcp import ClientSession
from mcp.client.sse import sse_client

from apiagent.config import Settings

logger = logging.getLogger(__name__)
DATABASE_ROLES = frozenset({"programmer", "admin"})


def can_access_database(context: dict | None) -> bool:
    return bool(context and context.get("user_id") and context.get("role") in DATABASE_ROLES)


class PostgresMCPClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def call(self, name: str, arguments: dict) -> str:
        # Fixed allowlist: tuning, write tools and arbitrary MCP calls are not exposed.
        if name not in {"list_schemas", "list_objects", "get_object_details", "execute_sql", "explain_query"}:
            raise ValueError("Unsupported PostgreSQL operation")
        if name == "explain_query" and (arguments.get("analyze") is not False or arguments.get("hypothetical_indexes")):
            raise ValueError("Only EXPLAIN without execution or hypothetical indexes is allowed")
        async with asyncio.timeout(self.settings.pgsql_timeout_seconds):
            async with sse_client(self.settings.pgsql_mcp_url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(name, arguments)
        text = "\n".join(block.text for block in result.content if block.type == "text")
        # Upstream sometimes returns errors as TextContent without isError.
        if result.isError or text.lstrip().lower().startswith("error:"):
            return "Ошибка PostgreSQL: запрос отклонён или недоступны таблица/подключение. Проверьте SQL и права пользователя БД."
        limit = self.settings.pgsql_max_chars
        return text if len(text) <= limit else text[:limit] + "\n[Результат сокращён до лимита символов]"


def create_postgres_readonly_tool(settings: Settings, client: PostgresMCPClient | None = None):
    backend = client or PostgresMCPClient(settings)

    @tool("postgresql_readonly")
    async def postgresql_readonly(
        operation: Literal["schemas", "tables", "describe", "query", "explain"],
        config: RunnableConfig,
        schema_name: str = "public",
        table: str = "",
        sql: str = "",
    ) -> str:
        """Read PostgreSQL schemas/data or get EXPLAIN without ANALYZE. No writes or query execution for plans."""
        context = config.get("configurable", {}).get("user_context")
        if not can_access_database(context):
            return "Доступ к PostgreSQL разрешён только программистам и администраторам."
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
        logger.info("PostgreSQL read: user_id=%s operation=%s", context["user_id"], operation)
        try:
            return await backend.call(name, args)
        except Exception:
            # Never include connection strings or upstream exception text in chat/logs.
            return "Сервис PostgreSQL недоступен или превышено время ожидания. Проверьте подключение и настройки."

    return postgresql_readonly
