"""HTTP-адаптер микросервиса APIAgent на базе FastAPI APIRouter."""

from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from apiagent.config import Settings
from apiagent.interfaces._protocols import IAgentService, IDocumentService

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    """Схема ответа эндпоинта /health.

    Attributes:
        status: Текущий статус сервиса ("healthy").
        model: Имя используемой LLM модели Ollama.
        documents_count: Число проиндексированных фрагментов документов.
    """

    status: str = Field(default="healthy", description="Статус работоспособности сервиса.")
    model: str = Field(description="Наименование активной языковой модели.")
    documents_count: int = Field(description="Количество документов/чанков в базе знаний.")


class DocumentCreateRequest(BaseModel):
    """Схема запроса на добавление документа в базу знаний.

    Attributes:
        title: Название или заголовок документа.
        content: Полный текст документа.
        metadata: Произвольные пользовательские метаданные.
    """

    title: str = Field(..., min_length=1, description="Заголовок документа.")
    content: str = Field(..., min_length=1, description="Содержимое документа для индексации.")
    metadata: dict[str, Any] | None = Field(default=None, description="Дополнительные метаданные документа.")


class DocumentCreateResponse(BaseModel):
    """Схема ответа при успешном добавлении документа.

    Attributes:
        document_id: Сгенерированный идентификатор документа.
        title: Заголовок документа.
        chunks_count: Число созданных текстовых фрагментов.
        status: Статус обработки ("indexed").
    """

    document_id: str = Field(description="Уникальный идентификатор созданного документа.")
    title: str = Field(description="Заголовок документа.")
    chunks_count: int = Field(description="Количество сохраненных чанков.")
    status: str = Field(default="indexed", description="Статус индексации документа.")


class ChatRequest(BaseModel):
    """Схема запроса к AI-агенту.

    Attributes:
        message: Текст вопроса или запроса пользователя.
        thread_id: Опциональный идентификатор существующей сессии диалога.
    """

    message: str = Field(..., min_length=1, description="Текст сообщения для агента.")
    thread_id: str | None = Field(default=None, description="Идентификатор сессии диалога.")


class ChatResponse(BaseModel):
    """Схема ответа AI-агента.

    Attributes:
        reply: Текстовый ответ языковой модели.
        thread_id: Идентификатор сохраненной сессии диалога.
    """

    reply: str = Field(description="Финальный ответ агента.")
    thread_id: str = Field(description="Идентификатор текущей сессии диалога.")


def create_api_router(
    agent_service: IAgentService,
    document_service: IDocumentService,
    settings: Settings,
) -> APIRouter:
    """Создание и конфигурирование FastAPI роутера с зависимостями.

    Args:
        agent_service: Сервис диалогового агента LangGraph (IAgentService).
        document_service: Сервис управления документами (IDocumentService).
        settings: Параметры конфигурации приложения (Settings).

    Returns:
        Сконфигурированный экземпляр APIRouter.
    """
    router = APIRouter()

    @router.get(
        "/health",
        response_model=HealthResponse,
        summary="Проверка работоспособности сервиса",
        tags=["System"],
    )
    async def get_health() -> HealthResponse:
        """Возвращает статус доступности сервиса и число проиндексированных фрагментов."""
        try:
            doc_count = document_service.count_documents()
            return HealthResponse(
                status="healthy",
                model=settings.ollama_model,
                documents_count=doc_count,
            )
        except Exception as exc:
            logger.error("Ошибка при проверке состояния сервиса: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Сбой сервиса хранения или моделей: {exc}",
            ) from exc

    @router.post(
        "/api/v1/documents",
        response_model=DocumentCreateResponse,
        status_code=status.HTTP_201_CREATED,
        summary="Добавление документа в базу знаний",
        tags=["Documents"],
    )
    async def create_document(request: DocumentCreateRequest) -> DocumentCreateResponse:
        """Принимает документ, разбивает на чанки и индексирует в Qdrant."""
        if not request.title.strip() or not request.content.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Заголовок и содержимое документа не могут быть пустыми.",
            )

        try:
            result = document_service.index_document(
                title=request.title,
                content=request.content,
                metadata=request.metadata,
            )
            return DocumentCreateResponse(
                document_id=result.document_id,
                title=result.title,
                chunks_count=result.chunks_count,
                status=result.status,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            logger.error("Ошибка при индексации документа: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Ошибка при сохранении документа: {exc}",
            ) from exc

    @router.post(
        "/api/v1/chat",
        response_model=ChatResponse,
        status_code=status.HTTP_200_OK,
        summary="Отправка сообщения диалоговому агенту",
        tags=["Chat"],
    )
    async def chat_with_agent(request: ChatRequest) -> ChatResponse:
        """Отправляет сообщение агенту LangGraph и возвращает ответ модели."""
        if not request.message.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Текст сообщения не может быть пустым.",
            )

        try:
            reply, thread_id = await agent_service.chat(
                message=request.message,
                thread_id=request.thread_id,
            )
            return ChatResponse(reply=reply, thread_id=thread_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            logger.error("Ошибка при обработке сообщения агентом: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Ошибка взаимодействия с агентом: {exc}",
            ) from exc

    return router
