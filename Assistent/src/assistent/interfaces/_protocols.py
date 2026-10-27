from typing import Any,Protocol,runtime_checkable

@runtime_checkable
class IAgentService(Protocol):
    """Интерфейс сервиса оркестрации AI-агента."""

    async def chat(
        self,
        message: str,
        thread_id: str | None = None,
        user_context: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """Отправка сообщения диалоговому агенту и получение ответа.

        Args:
            message: Текст входящего запроса пользователя.
            thread_id: Опциональный идентификатор существующей сессии диалога.
            user_context: Контекст безопасности пользователя (user_id, role, departments).

        Returns:
            Кортеж (ответ_агента, thread_id_сессии).
        """
        ...
