"""Протоколы и абстракции микросервиса viio_documents.

Определяет интерфейсы для инверсии зависимостей (DIP) и разделения интерфейсов (ISP):
IVectorStore, IDocumentService, IAgentService, а также схемы данных документов.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from pydantic import BaseModel, Field


class DocumentMetadata(BaseModel):
    """Метаданные проиндексированного документа.

    Attributes:
        document_id: Уникальный идентификатор документа.
        title: Заголовок документа.
        chunks_count: Количество текстовых фрагментов (чанков), на которые был разбит документ.
        status: Текущий статус обработки документа ("indexed").
    """

    document_id: str = Field(description="Уникальный идентификатор документа.")
    title: str = Field(description="Заголовок документа.")
    chunks_count: int = Field(description="Число фрагментов, сохраненных в векторной базе.")
    status: str = Field(default="indexed", description="Статус индексации документа.")


from viio_rag.contracts.interfaces import IVectorStore


@runtime_checkable
class IDocumentService(Protocol):
    """Интерфейс сервиса управления документами базы знаний."""

    def index_document(
        self,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        is_public: bool = False,
        allowed_roles: list[str] | None = None,
        allowed_departments: list[str] | None = None,
        owner_id: str | None = None,
    ) -> DocumentMetadata:
        """Индексация документа с разбиением на чанки и записью в векторное хранилище.

        Args:
            title: Заголовок или название документа.
            content: Полный текст документа.
            metadata: Дополнительные пользовательские метаданные (источник, автор и др.).
            is_public: Общедоступен ли документ для всех пользователей компании.
            allowed_roles: Список ролей, имеющих доступ к документу.
            allowed_departments: Список подразделений/отделов, имеющих доступ.
            owner_id: Идентификатор владельца/автора документа.

        Returns:
            Объект DocumentMetadata с результатом индексации.
        """
        ...

    def count_documents(self) -> int:
        """Получение количества фрагментов документов в базе знаний.

        Returns:
            Общее число проиндексированных фрагментов.
        """
        ...


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
