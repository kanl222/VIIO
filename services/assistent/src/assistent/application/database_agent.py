"""Ephemeral PostgreSQL assistant; coder and regular histories never see DB data."""

from __future__ import annotations

import asyncio
import uuid
import re

from assistent.application.agent_service import AgentService
from assistent.application.graph_builder import build_agent_graph
from assistent.prompts import DATABASE_SYSTEM_PROMPT
from assistent.infrastructure.tools.postgres_tool import can_access_database



DATABASE_REQUEST_RE = re.compile(
    r"(?:подключ[её]н\w*|тестов\w*|текущ\w*)\s+(?:\w+\s+){0,2}(?:postgres\w*|баз\w*|бд\b)|"
    r"(?:какие|покажи|перечисли|найди|проверь|структур\w*|схем\w*)[\s\S]{0,70}(?:таблиц\w*|схем\w*|колонк\w*|поля|баз\w*|бд\b|postgres)|"
    r"(?:оптимиз\w*|ускор\w*|план\s+запрос\w*|индекс\w*)[\s\S]{0,70}(?:бд\b|баз\w*|запрос\w*|таблиц\w*|postgres\w*|sql)|"
    r"(?:ошибк\w*|проблем\w*|лог\w*)[\s\S]{0,60}(?:postgres\w*|psycopg\w*|бд\b|баз\w*\s+данн|таблиц\w*)|"
    r"\b(?:psycopg\w*|pg_[\w]+|undefined_column|undefined_table)\b|"
    r"\bexplain\s+(?:analyze\s+)?(?:select|\b)",
    re.I,
)


def is_database_request(message: str) -> bool:
    """Live connection requests, not standalone SQL writing or database theory."""
    msg = message.strip()
    is_opt_or_diag = bool(re.search(
        r"(?:оптимиз\w*|ускор\w*|индекс\w*|explain|ошибк\w*|psycopg|план\s+запрос)[\s\S]{0,60}(?:бд\b|баз\w*|запрос|таблиц|postgres)",
        msg, re.I,
    ))
    if not is_opt_or_diag and re.search(r"^(?:напиши|создай|перепиши|объясни|что такое)\b", msg, re.I):
        return False
    return bool(is_opt_or_diag or DATABASE_REQUEST_RE.search(msg))


class DatabaseAgentService:
    def __init__(self, general, model, database_tool, enabled: bool) -> None:
        self.general = general
        self.model = model
        self.database_tool = database_tool
        self.enabled = enabled

    @property
    def checkpointer(self):
        return self.general.checkpointer

    def set_checkpointer(self, checkpointer):
        self.general.set_checkpointer(checkpointer)

    async def chat(self, message, thread_id=None, user_context=None):
        parts = message.strip().split(maxsplit=1)
        explicit = bool(parts and parts[0].lower() == "/db")
        if not explicit and not is_database_request(message):
            return await self.general.chat(message, thread_id, user_context)
        tid = thread_id or str(uuid.uuid4())
        if not can_access_database(user_context):
            return "Доступ к PostgreSQL разрешён только программистам и администраторам.", tid
        if not self.enabled:
            return "Инструмент PostgreSQL выключен. Настройте PGSQL_* и включите PGSQL_ENABLED.", tid
        if explicit and len(parts) == 1:
            return "Команды: /db schemas; /db tables public; /db describe public.orders; /db SELECT ...; /db explain SELECT ...; /db вопрос о базе.", tid
        task = parts[1].strip() if explicit else message.strip()
        args = None
        command, _, rest = task.partition(" ")
        if command.lower() == "schemas":
            args = {"operation": "schemas"}
        elif command.lower() == "tables":
            args = {"operation": "tables", "schema_name": rest.strip() or "public"}
        elif command.lower() == "describe":
            schema, _, table = rest.strip().rpartition(".")
            args = {"operation": "describe", "schema_name": schema or "public", "table": table}
        elif command.lower() == "explain":
            args = {"operation": "explain", "sql": rest}
        elif command.lower() in {"query", "select", "with", "insert", "update", "delete", "drop", "alter", "create", "truncate", "commit", "rollback", "copy"}:
            args = {"operation": "query", "sql": rest if command.lower() == "query" else task}
        try:
            async with asyncio.timeout(95):
                if args is not None:
                    result = await self.database_tool.ainvoke(args, config={"configurable": {"user_context": user_context}})
                    return str(result), tid
                # Без чекпоинтера: результаты БД нельзя повторно использовать после смены роли.
                service = AgentService(build_agent_graph(self.model, [self.database_tool], system_prompt=DATABASE_SYSTEM_PROMPT, model_first_tools=True))
                return await service.chat(task, tid, user_context)
        except TimeoutError:
            return "Превышено время ответа помощника PostgreSQL. Уточните запрос.", tid
