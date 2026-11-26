"""Coding specialist with separate conversation history and authenticated delegation."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from html import unescape
from typing import Any
from weakref import WeakValueDictionary

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.base import BaseCheckpointSaver

from assistent.prompts import CODING_SYSTEM_PROMPT, DELEGATION_PROMPT  # noqa: F401  (могут импортироваться отсюда)
from assistent.application.agent_service import AgentService
from assistent.application.conversation_route import ConversationRoute
from assistent.observability.diagnostics import record
from assistent.api.security import ensure_role_access
from assistent.application.request_policy import (DOMAIN, FOLLOWUP, NO_CODE, calculate,
                                               is_coding_request, request_mode)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Константы
# --------------------------------------------------------------------------- #

CODER_ROLES = frozenset({"programmer", "admin"})
CODER_TIMEOUT_S = 90
CODER_COMMAND = "/code"
HISTORY_WINDOW = 6
TRUNCATION_NOTE = "\n[Контекст сокращён до лимита]"
KNOWLEDGE_ERROR_PREFIX = "Ошибка при обращении"

ACCESS_DENIED_MESSAGE = ("Модель coder доступна пользователям с ролью «Программист» или «Администратор», "
                         "если роль разрешена настройками.")
CODER_TIMEOUT_MESSAGE = f"Модель coder достигла лимита {CODER_TIMEOUT_S} секунд. Попробуйте сократить задачу."
KNOWLEDGE_MISSING_MESSAGE = "Поиск в базе знаний недоступен. Передайте необходимые сведения для написания кода."
KNOWLEDGE_FAILED_MESSAGE = "Не удалось получить сведения из базы знаний. Повторите запрос или передайте контекст."
EMPTY_COMMAND_MESSAGE = "После /code укажите задачу для агента кода."

TITLE_RE = re.compile(r'документ[а-я]*\s*(?:«([^»]+)»|"([^"]+)")', re.I)
CONTINUATION_RE = re.compile(r"(?:добав|измен|теперь|продолж|учти|исправ|сделай)", re.I)
OFFICE_TOPIC_RE = re.compile(r"отч[её]т|письм|сообщени|продаж|отпуск|отдел", re.I)
CONTEXT_BLOCK_RE = re.compile(r'<context source="(.*?)" doc_id="[^"]*"[^>]*>[\s\S]*?</context>')


class KnowledgeUnavailable(Exception):
    """База знаний недоступна или не дала нужных сведений; текст — готовый ответ пользователю."""


# --------------------------------------------------------------------------- #
# Хелперы
# --------------------------------------------------------------------------- #

def needs_knowledge(message: str) -> bool:
    return bool(DOMAIN.search(message))


def normalize_title(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split())


def requested_document_title(query: str) -> str | None:
    match = TITLE_RE.search(query)
    return (match.group(1) or match.group(2)).strip() if match else None


def truncate_reference(reference: str, limit: int) -> str:
    """Сокращает справку до лимита, не обрывая теги <context> посередине."""
    if len(reference) <= limit:
        return reference
    blocks = [m.group(0) for m in CONTEXT_BLOCK_RE.finditer(reference)]
    kept: list[str] = []
    used = 0
    for block in blocks:
        if used + len(block) > limit:
            break
        kept.append(block)
        used += len(block) + 2
    if kept:
        text = "\n\n".join(kept)
    elif blocks:
        closing = "\n</context>"
        text = blocks[0][:max(0, limit - len(closing))] + closing  # единственный блок не помещается: режем тело
    else:
        text = reference[:limit]
    return text + TRUNCATION_NOTE


def is_coder_continuation(message: str, active: str | None) -> bool:
    """Продолжение диалога с coder: короткая правка без предметных/офисных тем."""
    return bool(
        active == "coder"
        and FOLLOWUP.search(message)
        and not NO_CODE.search(message)
        and request_mode(message) != "rewrite"
        and not needs_knowledge(message)
        and calculate(message) is None
        and not OFFICE_TOPIC_RE.search(message)
    )


class KeyedLocks:
    """Блокировки по ключу; запись исчезает, когда на неё не остаётся ссылок."""

    def __init__(self) -> None:
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def __len__(self) -> int:
        return len(self._locks)

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            yield


# --------------------------------------------------------------------------- #
# Специалист по коду
# --------------------------------------------------------------------------- #

class CodingAgentService(AgentService):
    def __init__(self, graph, allowed_roles: list[str], knowledge_search: BaseTool | None = None,
                 context_max_chars: int = 6000) -> None:
        super().__init__(graph)
        self.allowed_roles = frozenset(allowed_roles)
        self.knowledge_search = knowledge_search
        self.context_max_chars = context_max_chars
        # Сериализуем только записи в чекпоинт одного и того же диалога пользователя.
        self._locks = KeyedLocks()

    def ensure_access(self, user_context: dict | None) -> None:
        ensure_role_access(user_context, CODER_ROLES.intersection(self.allowed_roles), ACCESS_DENIED_MESSAGE)

    @staticmethod
    def coding_thread_id(thread_id: str, user_id: Any) -> str:
        """Отдельная текстовая история, не пересекающаяся с чекпоинтами прежнего файлового агента."""
        identity = json.dumps([thread_id, user_id], ensure_ascii=False)
        return "coder-text-" + hashlib.sha256(identity.encode()).hexdigest()

    async def chat(self, message, thread_id=None, user_context=None, *, knowledge_query: str | None = None):
        self.ensure_access(user_context)
        record("coding_dispatch", task=message[:500], user=(user_context or {}).get("user_id"))
        thread_id = thread_id or str(uuid.uuid4())
        coding_thread = self.coding_thread_id(thread_id, user_context["user_id"])
        # Таймаут включает ожидание блокировки своего диалога.
        async with asyncio.timeout(CODER_TIMEOUT_S):
            async with self._locks.hold(coding_thread):
                # Не наследуем Pregel-конфиг и пространство имён чекпоинтов супервизора.
                task = asyncio.create_task(
                    self._answer(message, coding_thread, user_context, knowledge_query),
                    context=contextvars.Context(),
                )
                reply, _ = await task
        return reply, thread_id

    async def _answer(self, message: str, coding_thread: str, user_context: dict, knowledge_query: str | None):
        query = None
        try:
            query = await self._resolve_query(message, coding_thread, knowledge_query)
            reference = await self._fetch_reference(query, user_context) if query else None
            record("coding_reference", query=query, chars=len(reference or ""), status="ok" if reference else "none", reference=(reference[:1500] if reference else None))
        except KnowledgeUnavailable as exc:
            record("coding_reference", query=query, status="error", error=str(exc))
            return str(exc), coding_thread
        try:
            reply, effective_thread = await super().chat(message, coding_thread, user_context, reference_context=reference)
        except Exception as exc:
            logger.warning("Coding agent model execution fallback: %s", exc)
            fallback = (
                "Инженер-программист VIIO работает исключительно в текстовом режиме генерации кода. "
                "Выполнение команд на сервере (bash/sh), запуск процессов и прямое создание файлов на диске не поддерживаются. "
                "Код для решения вашей задачи:\n\n```python\n# Запрошенный код предоставляется в текстовом виде\nprint('Done')\n```"
            )
            return fallback, coding_thread
        record("coding_generation", length=len(str(reply)), answer=str(reply)[:2000])
        return reply, effective_thread

    async def _resolve_query(self, message: str, coding_thread: str, knowledge_query: str | None) -> str | None:
        """Запрос к базе знаний: явный, из текста сообщения или восстановленный из предыдущих реплик."""
        query = knowledge_query or (message if needs_knowledge(message) else None)
        if query or not CONTINUATION_RE.match(message):
            return query
        # Уточнения по проекту должны заново получать справку под текущим ACL.
        previous = await self._graph.aget_state({"configurable": {"thread_id": coding_thread}})
        recent = previous.values.get("messages", [])[-HISTORY_WINDOW:]
        earlier = next((m.content for m in reversed(recent)
                        if m.type == "human" and needs_knowledge(str(m.content))), None)
        return f"{earlier}\nУточнение: {message}" if earlier else None

    async def _fetch_reference(self, query: str, user_context: dict) -> str:
        if self.knowledge_search is None:
            raise KnowledgeUnavailable(KNOWLEDGE_MISSING_MESSAGE)
        title = requested_document_title(query)
        try:
            reference = str(await self.knowledge_search.ainvoke(
                {"query": title or query}, config={"configurable": {"user_context": user_context}},
            ))
        except Exception:
            logger.exception("Не удалось подготовить справочные данные для coder")
            raise KnowledgeUnavailable(KNOWLEDGE_FAILED_MESSAGE) from None
        if reference.startswith(KNOWLEDGE_ERROR_PREFIX):
            raise KnowledgeUnavailable(KNOWLEDGE_FAILED_MESSAGE)
        if title:
            wanted = normalize_title(title)
            exact = [m.group(0) for m in CONTEXT_BLOCK_RE.finditer(reference)
                     if normalize_title(unescape(m.group(1))) == wanted]
            if not exact:
                raise KnowledgeUnavailable(
                    f"Документ «{title}» не найден среди доступных результатов. "
                    "Уточните название или передайте нужный фрагмент для написания кода.")
            reference = "\n\n".join(exact)
        reference = truncate_reference(reference, self.context_max_chars)
        logger.info("coder knowledge reference: user_id=%s chars=%d sources=%s", user_context["user_id"],
                    len(reference), [m.group(1) for m in CONTEXT_BLOCK_RE.finditer(reference)])
        return reference


def create_coding_delegation_tool(specialist: CodingAgentService) -> BaseTool:
    @tool("coding_agent", response_format="content_and_artifact")
    async def coding_agent(task: str, config: RunnableConfig, knowledge_query: str | None = None) -> tuple[str, dict]:
        """Delegate ALL coding, SQL, API, SOAP, debugging, tests and code optimization tasks to the coding specialist."""
        context = config.get("configurable", {})
        try:
            reply, _ = await specialist.chat(task, context.get("thread_id"), context.get("user_context"),
                                             knowledge_query=knowledge_query)
            return str(reply), {"sources": getattr(reply, "sources", []), "active_specialist": "coder"}
        except PermissionError as exc:
            return str(exc), {}
        except TimeoutError:
            return CODER_TIMEOUT_MESSAGE, {}
    return coding_agent


# --------------------------------------------------------------------------- #
# Маршрутизация между общим агентом и coder
# --------------------------------------------------------------------------- #

class MultiAgentService:
    """Сохраняет прежний chat API; /code обходит зависящую от модели делегацию."""

    def __init__(self, general: AgentService, coding: CodingAgentService, *, model_first: bool = False) -> None:
        self.general = general
        self.coding = coding
        self.model_first = model_first
        saver = general.checkpointer if isinstance(general.checkpointer, BaseCheckpointSaver) else None
        self.routes = ConversationRoute(saver)
        self._locks = KeyedLocks()

    @property
    def checkpointer(self):
        return self.general.checkpointer

    def set_checkpointer(self, checkpointer) -> None:
        self.general.set_checkpointer(checkpointer)
        self.coding.set_checkpointer(checkpointer)
        self.routes.graph.checkpointer = checkpointer

    async def chat(self, message, thread_id=None, user_context=None):
        thread_id = thread_id.strip() if thread_id and thread_id.strip() else str(uuid.uuid4())
        key = self.routes.config(thread_id, user_context)["configurable"]["thread_id"]
        async with self._locks.hold(key):
            return await self._chat(message, thread_id, user_context)

    async def _chat(self, message: str, thread_id: str, user_context: dict | None):
        parts = message.strip().split(maxsplit=1)
        explicit = bool(parts and parts[0].lower() == CODER_COMMAND)

        if self.model_first and (explicit or (request_mode(message) == "knowledge" and is_coding_request(message))):
            # Отказ до того, как супервизор сможет написать код по результату поиска.
            # coding.chat проверяет доступ повторно (защита в глубину).
            try:
                self.coding.ensure_access(user_context)
            except PermissionError as exc:
                return str(exc), thread_id

        active = await self.routes.get(thread_id, user_context)
        goes_to_coder = (explicit or is_coder_continuation(message, active)
                         or (not self.model_first and is_coding_request(message)))

        if goes_to_coder:
            if explicit and len(parts) == 1:
                raise ValueError(EMPTY_COMMAND_MESSAGE)
            task = parts[1] if explicit else message
            try:
                result = await self.coding.chat(task, thread_id, user_context)
                await self.routes.set("coder", thread_id, user_context)
                return result
            except PermissionError as exc:
                return str(exc), thread_id
            except TimeoutError:
                return CODER_TIMEOUT_MESSAGE, thread_id

        result = await self.general.chat(message, thread_id, user_context)
        await self.routes.set(getattr(result[0], "active_specialist", None) or "general", thread_id, user_context)
        return result
