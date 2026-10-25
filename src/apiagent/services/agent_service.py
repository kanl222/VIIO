"""Сервис оркестрации диалогового агента LangGraph."""

from __future__ import annotations

import logging
from typing import Any
import uuid
from langgraph.graph.state import CompiledStateGraph

from apiagent.interfaces._protocols import IAgentService

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

    async def chat(self, message: str, thread_id: str | None = None) -> tuple[str, str]:
        """Отправка сообщения агенту в рамках указанной или вновь созданной сессии.

        Args:
            message: Текст запроса пользователя.
            thread_id: Идентификатор сессии. Если не указан, создается новый UUID.

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
        config = {"configurable": {"thread_id": effective_thread_id}}

        logger.info("Обработка сообщения агентом (thread_id: %s)", effective_thread_id)

        try:
            result = await self._graph.ainvoke(
                {"messages": [("user", clean_message)]},
                config=config,
            )
        except Exception as exc:
            logger.error(
                "Ошибка при вызове графа агента (thread_id: %s): %s",
                effective_thread_id,
                exc,
            )
            raise

        messages = result.get("messages", [])
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

        return reply, effective_thread_id
