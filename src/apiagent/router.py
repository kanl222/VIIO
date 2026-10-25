"""HTTP-адаптер микросервиса APIAgent на базе FastAPI APIRouter."""

from __future__ import annotations

import logging
from typing import Any
import anyio.to_thread
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from apiagent.config import Settings
from apiagent.interfaces._protocols import IAgentService, IDocumentService

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    """Схема ответа эндпоинта /health.

    Attributes:
        status: Текущий статус сервиса ("healthy").
        provider: Активный провайдер языковых моделей ("lmstudio" или "ollama").
        model: Имя используемой LLM модели.
        documents_count: Число проиндексированных фрагментов документов.
    """

    status: str = Field(default="healthy", description="Статус работоспособности сервиса.")
    provider: str = Field(default="lmstudio", description="Активный провайдер языковых моделей.")
    model: str = Field(description="Наименование активной языковой модели.")
    documents_count: int = Field(description="Количество документов/чанков в базе знаний.")


class UserContext(BaseModel):
    """Схема контекста пользователя для разграничения доступа к знаниям (RBAC).

    Attributes:
        user_id: Идентификатор пользователя.
        role: Роль пользователя в системе (например, 'admin', 'employee', 'guest').
        departments: Список подразделений/отделов пользователя.
    """

    user_id: str | None = Field(default=None, description="Идентификатор пользователя.")
    role: str = Field(default="employee", description="Роль пользователя в системе.")
    departments: list[str] = Field(
        default_factory=lambda: ["general"],
        description="Список подразделений/отделов пользователя.",
    )


class DocumentCreateRequest(BaseModel):
    """Схема запроса на добавление документа в базу знаний.

    Attributes:
        title: Название или заголовок документа.
        content: Полный текст документа.
        metadata: Произвольные пользовательские метаданные.
        is_public: Флаг публичной доступности документа для всех пользователей.
        allowed_roles: Список ролей, имеющих доступ к документу.
        allowed_departments: Список подразделений, имеющих доступ к документу.
        owner_id: Идентификатор владельца/автора документа.
    """

    title: str = Field(..., min_length=1, description="Заголовок документа.")
    content: str = Field(..., min_length=1, description="Содержимое документа для индексации.")
    metadata: dict[str, Any] | None = Field(default=None, description="Дополнительные метаданные документа.")
    is_public: bool = Field(default=False, description="Доступен ли документ публично для всех.")
    allowed_roles: list[str] = Field(
        default_factory=lambda: ["employee"],
        description="Список ролей с правом доступа к документу.",
    )
    allowed_departments: list[str] = Field(
        default_factory=lambda: ["general"],
        description="Список подразделений с правом доступа к документу.",
    )
    owner_id: str | None = Field(default=None, description="Идентификатор владельца документа.")


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
        user_context: Контекст безопасности пользователя для фильтрации знаний (RBAC).
    """

    message: str = Field(..., min_length=1, description="Текст сообщения для агента.")
    thread_id: str | None = Field(default=None, description="Идентификатор сессии диалога.")
    user_context: UserContext | None = Field(
        default=None,
        description="Контекст безопасности пользователя для фильтрации знаний.",
    )


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

    def verify_internal_token(
        x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
    ) -> None:
        """Проверка секретного токена межсервисной аутентификации.

        Raises:
            HTTPException: 401 при несовпадении или отсутствии токена, если токен настроен.
        """
        if settings.internal_api_key:
            if not x_internal_token or x_internal_token != settings.internal_api_key:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Недействительный или отсутствующий межсервисный токен (X-Internal-Token).",
                )

    @router.get(
        "/health",
        response_model=HealthResponse,
        summary="Проверка работоспособности сервиса",
        tags=["System"],
    )
    async def get_health() -> HealthResponse:
        """Возвращает статус доступности сервиса и число проиндексированных фрагментов."""
        try:
            doc_count = await anyio.to_thread.run_sync(document_service.count_documents)
            active_model = (
                settings.lmstudio_model
                if settings.llm_provider == "lmstudio"
                else settings.ollama_model
            )
            return HealthResponse(
                status="healthy",
                provider=settings.llm_provider,
                model=active_model,
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
        dependencies=[Depends(verify_internal_token)],
    )
    async def create_document(request: DocumentCreateRequest) -> DocumentCreateResponse:
        """Принимает документ, разбивает на чанки и индексирует в Qdrant с атрибутами доступа."""
        if not request.title.strip() or not request.content.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Заголовок и содержимое документа не могут быть пустыми.",
            )

        try:
            result = await anyio.to_thread.run_sync(
                document_service.index_document,
                request.title,
                request.content,
                request.metadata,
                request.is_public,
                request.allowed_roles,
                request.allowed_departments,
                request.owner_id,
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
        dependencies=[Depends(verify_internal_token)],
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
                user_context=request.user_context.model_dump() if request.user_context else None,
            )
            return ChatResponse(reply=reply, thread_id=thread_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            exc_msg = str(exc) or repr(exc) or type(exc).__name__
            logger.error("Ошибка при обработке сообщения агентом: %s", exc_msg, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Ошибка взаимодействия с агентом: {exc_msg}",
            ) from exc

    return router
