"""Сервис оркестрации диалогового агента LangGraph."""

from __future__ import annotations

import logging
from typing import Any
import uuid
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from assistent.interfaces._protocols import IAgentService
from assistent.services.answer_sources import AgentReply, clean_answer
from assistent.services.graph_builder import scope_key

logger = logging.getLogger(__name__)


class AgentService(IAgentService):
    """Сервис управления сессиями и взаимодействием с диалоговым агентом LangGraph.

    Attributes:
        _graph: Скомпилированный граф LangGraph (CompiledStateGraph).
    """

    def __init__(self, graph: CompiledStateGraph) -> None:
        """Инициализация сервиса агента.

        Args:
            graph: Скомпилированный граф агента LangGraph.
        """
        self._graph = graph

    @property
    def checkpointer(self) -> BaseCheckpointSaver | None:
        """Текущий используемый чекпоинтер сессий графа агента."""
        return getattr(self._graph, "checkpointer", None)

    def set_checkpointer(self, checkpointer: BaseCheckpointSaver) -> None:
        """Динамическая замена или установка чекпоинтера в графе агента.

        Args:
            checkpointer: Экземпляр чекпоинтера (например, AsyncPostgresSaver или MemorySaver).
        """
        self._graph.checkpointer = checkpointer

    async def chat(
        self,
        message: str,
        thread_id: str | None = None,
        user_context: dict[str, Any] | None = None,
        *, reference_context: str | None = None,
    ) -> tuple[str, str]:
        """Отправка сообщения агенту в рамках указанной или вновь созданной сессии.

        Args:
            message: Текст запроса пользователя.
            thread_id: Идентификатор сессии. Если не указан, создается новый UUID.
            user_context: Контекст безопасности пользователя (user_id, role, departments).

        Returns:
            Кортеж (ответ_агента, thread_id_сессии).

        Raises:
            ValueError: Если текст сообщения пуст или состоит только из пробелов.
            Exception: При непредвиденных ошибках выполнения графа.
        """
        clean_message = message.strip()
        if not clean_message:
            raise ValueError("Сообщение не может быть пустым")

        effective_thread_id = thread_id.strip() if thread_id and thread_id.strip() else str(uuid.uuid4())
        config = {
            "configurable": {
                "thread_id": effective_thread_id,
                "user_context": user_context or {},
                "user_message": clean_message,
            }
        }

        logger.info(
            "Обработка сообщения агентом (thread_id: %s, role: %s)",
            effective_thread_id,
            (user_context or {}).get("role", "guest"),
        )

        try:
            from assistent.services.streaming import event_sink, invoke_streaming
            inputs = {"messages": [("user", clean_message)]}
            context = {"access_scope": scope_key(user_context)+':'+str((user_context or {}).get('_knowledge_epoch','')), 'user_context': user_context or {}, 'user_notes': (user_context or {}).get('_user_notes', ''), **({"reference_context": reference_context} if reference_context else {})}
            if sink := event_sink.get():
                result = await invoke_streaming(self._graph, inputs, config, context, sink)
            else:
                result = await self._graph.ainvoke(inputs, config=config, context=context)
        except Exception as exc:
            exc_msg = str(exc) or repr(exc) or type(exc).__name__
            logger.error(
                "Ошибка при вызове графа агента (thread_id: %s): %s",
                effective_thread_id,
                exc_msg,
                exc_info=True,
            )
            raise RuntimeError(exc_msg) from exc

        messages = result.get("messages", [])
        interrupts = result.get("__interrupt__", [])
        if interrupts:
            int_val = interrupts[0].value
            prompt_text = int_val.get("prompt") if isinstance(int_val, dict) else str(int_val)
            return AgentReply(
                prompt_text or "Требуется подтверждение операции.",
                sources=[],
                active_specialist=None,
                saved_documents=[],
                interrupt=int_val,
            ), effective_thread_id

        if not messages:
            return "", effective_thread_id

        last_message = messages[-1]
        raw_content: Any = getattr(last_message, "content", "")

        if isinstance(raw_content, str):
            reply = raw_content
        elif isinstance(raw_content, list):
            reply = "".join(
                item.get("text", "") if isinstance(item, dict) else str(item)
                for item in raw_content
            )
        else:
            reply = str(raw_content)

        sources = getattr(last_message, "response_metadata", {}).get("sources", [])
        active = getattr(last_message, "response_metadata", {}).get("active_specialist")
        saved = getattr(last_message, 'response_metadata', {}).get('saved_documents', [])
        return AgentReply(clean_answer(reply, sources), sources, active, saved_documents=saved), effective_thread_id

    async def resume(
        self,
        thread_id: str,
        value: Any,
        user_context: dict[str, Any] | None = None,
    ) -> tuple[AgentReply, str]:
        """Возобновляет приостановленную сессию графа ответом пользователя."""
        from langgraph.types import Command
        effective_thread_id = thread_id.strip()
        config = {
            "configurable": {
                "thread_id": effective_thread_id,
                "user_context": user_context or {},
            }
        }
        context = {
            "access_scope": scope_key(user_context) + ":" + str((user_context or {}).get("_knowledge_epoch", "")),
            "user_context": user_context or {},
        }
        try:
            result = await self._graph.ainvoke(Command(resume=value), config=config, context=context)
        except Exception as exc:
            logger.error("Ошибка при возобновлении графа (thread_id: %s): %s", effective_thread_id, exc, exc_info=True)
            raise RuntimeError(str(exc)) from exc

        interrupts = result.get("__interrupt__", [])
        if interrupts:
            int_val = interrupts[0].value
            prompt_text = int_val.get("prompt") if isinstance(int_val, dict) else str(int_val)
            return AgentReply(
                prompt_text or "Требуется подтверждение операции.",
                sources=[],
                active_specialist=None,
                saved_documents=[],
                interrupt=int_val,
            ), effective_thread_id

        messages = result.get("messages", [])
        if not messages:
            return AgentReply("", []), effective_thread_id

        last_message = messages[-1]
        raw_content: Any = getattr(last_message, "content", "")
        reply = raw_content if isinstance(raw_content, str) else str(raw_content)
        sources = getattr(last_message, "response_metadata", {}).get("sources", [])
        active = getattr(last_message, "response_metadata", {}).get("active_specialist")
        saved = getattr(last_message, "response_metadata", {}).get("saved_documents", [])
        return AgentReply(clean_answer(reply, sources), sources, active, saved_documents=saved), effective_thread_id

    async def get_pending_interrupt(self, thread_id: str) -> Any | None:
        """Проверяет, ожидает ли сессия интерактивного подтверждения пользователем."""
        state = await self._graph.aget_state({"configurable": {"thread_id": thread_id}})
        if state and state.tasks:
            for task in state.tasks:
                if getattr(task, "interrupts", None):
                    return task.interrupts[0].value
        return None
