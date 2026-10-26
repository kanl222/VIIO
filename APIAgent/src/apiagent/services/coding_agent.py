"""Coding specialist with separate conversation history and authenticated delegation."""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import re
import uuid
from weakref import WeakValueDictionary

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool

from apiagent.prompts import CODING_SYSTEM_PROMPT, DELEGATION_PROMPT
from apiagent.services.agent_service import AgentService

logger = logging.getLogger(__name__)

# Keep ordinary domain questions (e.g. "код рецепта") in the knowledge agent.
# Explicit technical requests must not depend on a small model emitting a tool call.
CODING_INTENT = re.compile(
    r"\b(?:sql|soap|rest|graphql|wsdl|git|python|javascript|typescript|csharp|java|"
    r"pytest|npm|docker|langgraph|fastapi|react|api)\b|"
    r"```|Traceback \(most recent call last\)|"
    r"\b[\w/-]+\.(?:py|tsx?|jsx?|cs|sql|go|rs|java|wsdl)\b|"
    r"исходник|исходн[а-я]*\s+код|программирован|рефактор|отладк|"
    r"(?:напиш|напис|созда|исправ|перепиш|перепис|проверь|провер|измен|добав)[\s\S]{0,80}"
    r"(?:\bкод\b|функци|метод|юнит.?тест|unit.?test)|"
    r"(?:оптимиз|ускор)[\s\S]{0,60}(?:\bкод\b|функци|алгоритм)|"
    r"(?:ошибк|баг)[\s\S]{0,50}(?:\bкод[ае]?\b|сборк|компиля|функци)",
    re.IGNORECASE,
)


def is_coding_request(message: str) -> bool:
    if re.search(r"код\s+(?:писать\s+)?не\s+(?:нуж|надо)|не\s+(?:пиши|писать)\s+код|без\s+(?:написания\s+)?кода", message, re.I):
        if needs_knowledge(message):
            return False
    return bool(CODING_INTENT.search(message))


def needs_knowledge(message: str) -> bool:
    return bool(re.search(r"баз[аеуы]\s+знани|документ|регламент|наш(?:ей|ем)\s+(?:систем|проект)|ис\s*ло|ssrs|availablereports|\bviio\b", message, re.I))


class CodingAgentService(AgentService):
    def __init__(self, graph, allowed_roles: list[str], knowledge_search: BaseTool | None = None, context_max_chars: int = 6000) -> None:
        super().__init__(graph)
        self.allowed_roles = allowed_roles
        self.knowledge_search = knowledge_search
        self.context_max_chars = context_max_chars
        # Serialize only writes to the same user's conversation checkpoint.
        # Entries disappear once the last holder/waiter releases its reference.
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    async def chat(self, message, thread_id=None, user_context=None, *, knowledge_query: str | None = None):
        if (not user_context or not user_context.get("user_id") or user_context.get("role") not in {"programmer", "admin"}
                or user_context.get("role") not in self.allowed_roles):
            raise PermissionError("Модель coder доступна пользователям с ролью «Программист» или «Администратор», если роль разрешена настройками.")
        thread_id = thread_id or str(uuid.uuid4())
        identity = json.dumps([thread_id, user_context["user_id"]], ensure_ascii=False)
        # Separate text-only history from the former file/tool agent's checkpoints.
        coding_thread = "coder-text-" + hashlib.sha256(identity.encode()).hexdigest()
        lock = self._locks.setdefault(coding_thread, asyncio.Lock())
        async with asyncio.timeout(90):
            async with lock:
                # Do not inherit the supervisor's Pregel config/checkpoint namespace.
                task = asyncio.create_task(
                    self._answer(message, coding_thread, user_context, knowledge_query), context=contextvars.Context(),
                )
                reply, _ = await task
        return reply, thread_id

    async def _answer(self, message, coding_thread, user_context, knowledge_query):
        query = knowledge_query or (message if needs_knowledge(message) else None)
        if not query and re.match(r"(?:добав|измен|теперь|продолж|учти|исправ|сделай)", message, re.I):
            # Project-specific continuations must refresh references under the current ACL.
            previous = await self._graph.aget_state({"configurable": {"thread_id": coding_thread}})
            earlier = next((m.content for m in reversed(previous.values.get("messages", [])[-6:])
                            if m.type == "human" and needs_knowledge(str(m.content))), None)
            if earlier:
                query = str(earlier) + "\nУточнение: " + message
        reference = None
        if query:
            title_match = re.search(r'документ[а-я]*\s*[«"]([^»"]+)[»"]', query, re.I)
            requested_title = title_match.group(1).strip() if title_match else None
            if self.knowledge_search is None:
                return "Поиск в базе знаний недоступен. Передайте необходимые сведения для написания кода.", coding_thread
            try:
                reference = str(await self.knowledge_search.ainvoke(
                    {"query": requested_title or query}, config={"configurable": {"user_context": user_context}},
                ))
            except Exception:
                logger.exception("Не удалось подготовить справочные данные для coder")
                return "Не удалось получить сведения из базы знаний. Повторите запрос или передайте контекст.", coding_thread
            if reference.startswith("Ошибка при обращении"):
                return "Не удалось получить сведения из базы знаний. Повторите запрос или передайте контекст.", coding_thread
            if requested_title:
                blocks = re.finditer(r'<context source="(.*?)" doc_id="[^"]*"[^>]*>[\s\S]*?</context>', reference)
                normalize = lambda text: " ".join(text.casefold().replace("ё", "е").split())
                exact = [block.group(0) for block in blocks if normalize(block.group(1)) == normalize(requested_title)]
                if not exact:
                    return f"Документ «{requested_title}» не найден среди доступных результатов. Уточните название или передайте нужный фрагмент для написания кода.", coding_thread
                reference = "\n\n".join(exact)
            if len(reference) > self.context_max_chars:
                reference = reference[:self.context_max_chars] + "\n[Контекст сокращён до лимита]"
            logger.info("coder knowledge reference: user_id=%s chars=%d sources=%s", user_context["user_id"], len(reference),
                        re.findall(r'<context source="(.*?)" doc_id=', reference))
        return await super().chat(message, coding_thread, user_context, reference_context=reference)


def create_coding_delegation_tool(specialist: CodingAgentService) -> BaseTool:
    @tool("coding_agent")
    async def coding_agent(task: str, config: RunnableConfig, knowledge_query: str | None = None) -> str:
        """Delegate ALL coding, SQL, API, SOAP, debugging, tests and code optimization tasks to the coding specialist."""
        context = config.get("configurable", {})
        try:
            reply, _ = await specialist.chat(task, context.get("thread_id"), context.get("user_context"), knowledge_query=knowledge_query)
            return reply
        except PermissionError as exc:
            return str(exc)
        except TimeoutError:
            return "Модель coder достигла лимита 90 секунд. Попробуйте сократить задачу."
    return coding_agent


class MultiAgentService:
    """Keep the existing chat API; /code bypasses model-dependent delegation."""

    def __init__(self, general: AgentService, coding: CodingAgentService) -> None:
        self.general = general
        self.coding = coding

    @property
    def checkpointer(self):
        return self.general.checkpointer

    def set_checkpointer(self, checkpointer):
        self.general.set_checkpointer(checkpointer)
        self.coding.set_checkpointer(checkpointer)

    async def chat(self, message, thread_id=None, user_context=None):
        parts = message.strip().split(maxsplit=1)
        explicit = bool(parts and parts[0].lower() == "/code")
        if explicit or is_coding_request(message):
            if explicit and len(parts) == 1:
                raise ValueError("После /code укажите задачу для агента кода.")
            thread_id = thread_id or str(uuid.uuid4())
            try:
                return await self.coding.chat(parts[1] if explicit else message, thread_id, user_context)
            except PermissionError as exc:
                return str(exc), thread_id
            except TimeoutError:
                return "Модель coder достигла лимита 90 секунд. Попробуйте сократить задачу.", thread_id
        return await self.general.chat(message, thread_id, user_context)
