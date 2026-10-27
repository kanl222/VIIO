"""HTTP-адаптер микросервиса assistent на базе FastAPI APIRouter."""

from __future__ import annotations

import logging
import mimetypes
import uuid
import inspect
from hmac import compare_digest
from pathlib import Path
from urllib.parse import quote
from typing import Any
import anyio.to_thread
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, Field, ConfigDict

from assistent.config import Settings
from assistent.interfaces._protocols import IAgentService, IDocumentService
from assistent.services.pdf_extraction import PdfExtractionService, PdfExtractionError
from assistent.services.document_access import can_read_document
from assistent.services.code_documents import is_code_file
from assistent.services.document_catalog import DocumentCatalogService

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


class ChatSource(BaseModel):
    """Server provenance only; no model-generated download URL or vector score."""

    title: str
    document_id: str


class ChatResponse(BaseModel):
    """Схема ответа AI-агента.

    Attributes:
        reply: Текстовый ответ языковой модели.
        thread_id: Идентификатор сохраненной сессии диалога.
    """

    reply: str = Field(description="Финальный ответ агента.")
    thread_id: str = Field(description="Идентификатор текущей сессии диалога.")
    sources: list[ChatSource] = Field(default_factory=list, description="Материалы, предоставленные для текущего ответа; не доказательство каждого утверждения.")


class ReviewApprovalRequest(BaseModel):
    content: str = Field(min_length=1, max_length=500000)
    review_token: str = Field(pattern=r'^[0-9a-f]{32}$')


class PublicationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=50000)


class PublicQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=4000)


class PublishedIdsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    publication_ids: list[uuid.UUID] = Field(max_length=4)


def create_api_router(
    agent_service: IAgentService,
    document_service: IDocumentService,
    settings: Settings,
    catalog_service: Any | None = None,
    file_store: Any | None = None,
    bulk_queue: Any | None = None,
    ingestion_service: Any | None = None,
    knowledge_data: Any | None = None,
    consultant_service: Any | None = None,
) -> APIRouter:
    """Создание и конфигурирование FastAPI роутера с зависимостями.

    Args:
        agent_service: Сервис диалогового агента LangGraph (IAgentService).
        document_service: Сервис управления документами (IDocumentService).
        settings: Параметры конфигурации приложения (Settings).
        catalog_service: Опциональный DocumentCatalogService (каталог PostgreSQL).
        file_store: Опциональный IFileStore (хранилище оригиналов).
        bulk_queue: Опциональный BulkQueue (очередь пакетной загрузки).

    Returns:
        Сконфигурированный экземпляр APIRouter.
    """
    router = APIRouter()
    pdf_service = PdfExtractionService(settings)

    def require_catalog():
        if catalog_service is None or isinstance(catalog_service, DocumentCatalogService) and not catalog_service.available:
            raise HTTPException(503, "Каталог документов недоступен.")

    async def resolved(value):
        return await value if inspect.isawaitable(value) else value

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

    @router.post("/api/v1/documents/extract-pdf", dependencies=[Depends(verify_internal_token)], tags=["Documents"])
    async def extract_pdf(request: Request) -> dict:
        """Extract preview text only; the administrator indexes it with explicit ACL later."""
        if request.headers.get("content-type", "").split(";")[0] != "application/pdf":
            raise HTTPException(415, "Передайте PDF с Content-Type: application/pdf.")
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > settings.pdf_max_bytes:
                raise HTTPException(413, "Размер PDF превышает лимит 10 МБ.")
            data.extend(chunk)
        try:
            return await pdf_service.extract(bytes(data))
        except PdfExtractionError as exc:
            raise HTTPException(exc.status, exc.detail) from None

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
            if ingestion_service is not None:
                require_catalog()
                result = await ingestion_service.index_text(request)
            else:
                result = await anyio.to_thread.run_sync(
                    document_service.index_document, request.title, request.content,
                    request.metadata, request.is_public, request.allowed_roles,
                    request.allowed_departments, request.owner_id,
                )
            return DocumentCreateResponse(
                document_id=result.document_id,
                title=result.title,
                chunks_count=result.chunks_count,
                status=result.status,
            )
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            logger.error("Ошибка при индексации документа: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Не удалось сохранить документ. Повторите попытку позже.",
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
            return ChatResponse(reply=str(reply), thread_id=thread_id, sources=getattr(reply, "sources", []))
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

    # ------------------------------------------------------------------
    # Каталог документов
    # ------------------------------------------------------------------

    @router.get(
        "/api/v1/documents/catalog",
        tags=["Documents"],
        summary="Список документов каталога",
        dependencies=[Depends(verify_internal_token)],
    )
    async def list_catalog(
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        status_filter: str | None = Query(default=None, alias="status"),
        owner_id: str | None = Query(default=None),
    ) -> dict:
        """Постраничный список документов в каталоге PostgreSQL."""
        require_catalog()
        result = await catalog_service.list_documents(
            page=page, page_size=page_size,
            status=status_filter, owner_id=owner_id,
        )
        return {
            "items": [_catalog_record_to_dict(r) for r in result.items],
            "total": result.total,
            "page": result.page,
            "page_size": result.page_size,
        }

    @router.get(
        "/api/v1/documents/catalog/{doc_id}",
        tags=["Documents"],
        summary="Карточка документа",
        dependencies=[Depends(verify_internal_token)],
    )
    async def get_catalog_item(doc_id: str) -> dict:
        """Метаданные одного документа из каталога."""
        require_catalog()
        record = await catalog_service.get(doc_id)
        if record is None:
            raise HTTPException(404, f"Документ {doc_id!r} не найден в каталоге.")
        return _catalog_record_to_dict(record)

    @router.get(
        "/api/v1/documents/download/{doc_id}",
        tags=["Documents"],
        summary="Скачать оригинал документа",
        dependencies=[Depends(verify_internal_token)],
    )
    async def download_document(doc_id: str, user_role: str = Query(default="employee"),
                                user_id: str | None = Query(default=None), departments: str = Query(default="")) -> FastAPIResponse:
        """Авторизованная выдача оригинала файла (байт-в-байт).

        Проверяет права доступа по роли и возвращает файл с корректным Content-Type.
        Для получения файла WebChat передаёт роль пользователя в query-параметре.
        """
        require_catalog()
        if file_store is None:
            raise HTTPException(503, "Хранилище оригиналов не инициализировано.")

        record = await catalog_service.get(doc_id)
        if record is None:
            raise HTTPException(404, "Документ не найден.")

        # Проверка доступа: публичный ИЛИ роль совпадает ИЛИ admin
        if not can_read_document(record, {"role": user_role, "user_id": user_id, "departments": [d for d in departments.split(',') if d]}):
            raise HTTPException(403, "Доступ к документу запрещён.")

        if not record.storage_key:
            raise HTTPException(404, "Оригинал файла не сохранён. Документ был добавлен без загрузки файла.")

        try:
            data = await anyio.to_thread.run_sync(file_store.load, record.storage_key)
        except FileNotFoundError:
            raise HTTPException(404, "Файл не найден в хранилище.")

        media_type = record.mime_type or "application/octet-stream"
        safe_title = record.title.replace('\r', '').replace('\n', '').replace('"', "'")[:100]
        ext = _ext_for_mime(media_type)
        filename = (record.original_filename or f"{safe_title}{ext}").replace('\\', '/').rsplit('/', 1)[-1].replace('\r', '').replace('\n', '')

        return FastAPIResponse(
            content=data,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename=\"document{ext}\"; filename*=UTF-8''{quote(filename, safe='')}", "X-Content-Type-Options": "nosniff"},
        )

    # ------------------------------------------------------------------
    # Массовая загрузка
    # ------------------------------------------------------------------

    @router.post(
        "/api/v1/documents/bulk-upload",
        tags=["Documents"],
        summary="Пакетная загрузка документов",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(verify_internal_token)],
    )
    async def bulk_upload(
        files: list[UploadFile] = File(...),
        is_public: bool = Query(default=False),
        allowed_roles: str = Query(default="employee"),
        allowed_departments: str = Query(default="general"),
        owner_id: str | None = Query(default=None),
        version_parent_id: str | None = Query(default=None),
    ) -> dict:
        """Принять несколько файлов и поставить их в очередь индексации.

        Возвращает список task_id для отслеживания прогресса.
        """
        if bulk_queue is None:
            raise HTTPException(503, "Очередь пакетной загрузки не инициализирована.")
        if ingestion_service is not None:
            require_catalog()

        max_files = settings.bulk_max_files
        if len(files) > max_files:
            raise HTTPException(400, f"Максимум {max_files} файлов за один запрос.")

        from assistent.services.bulk_queue import BulkUploadTask

        roles = [r.strip() for r in allowed_roles.split(",") if r.strip()]
        depts = [d.strip() for d in allowed_departments.split(",") if d.strip()]
        if version_parent_id:
            require_catalog()
            if len(files) != 1:
                raise HTTPException(400, 'Для новой версии выберите один файл')
            parent = await catalog_service.get(version_parent_id)
            if parent is None:
                raise HTTPException(404, 'Документ не найден')
            if not parent.is_active or parent.status not in {'ready', 'indexed'}:
                raise HTTPException(409, 'Актуальная версия изменилась. Обновите каталог.')
            roles, depts = parent.allowed_roles, parent.allowed_departments
            owner_id, is_public = parent.owner_id, parent.is_public

        tasks_info = []
        for upload in files:
            filename = (upload.filename or "document").replace('\\', '/').rsplit('/', 1)[-1][:255]
            data = await upload.read(settings.bulk_max_file_bytes + 1)
            await upload.close()

            if len(data) > settings.bulk_max_file_bytes:
                tasks_info.append({
                    "task_id": None,
                    "filename": filename,
                    "status": "error",
                    "error": f"Файл превышает лимит {settings.bulk_max_file_bytes // 1024 // 1024} МБ.",
                })
                continue

            suffix = Path(filename).suffix.lower()
            mime = {'.pdf': 'application/pdf', '.txt': 'text/plain', '.md': 'text/markdown', '.markdown': 'text/markdown'}.get(suffix)
            if is_code_file(filename):
                mime = 'text/plain'
            if mime is None or not data:
                tasks_info.append({"task_id": None, "filename": filename, "status": "error", "error": "Поддерживаются непустые PDF, TXT и Markdown."})
                continue
            task_id = uuid.uuid4().hex
            title = filename.rsplit(".", 1)[0][:255] if "." in filename else filename[:255]

            task = BulkUploadTask(
                task_id=task_id,
                filename=filename,
                data=data,
                title=title,
                owner_id=owner_id,
                is_public=is_public,
                mime_type=mime,
                allowed_roles=roles,
                allowed_departments=depts,
                version_parent_id=version_parent_id,
                kind='code' if is_code_file(filename) else 'document',
            )
            try:
                result = await resolved(bulk_queue.enqueue(task))
                tasks_info.append({
                    "task_id": result.task_id,
                    "filename": result.filename,
                    "status": result.status,
                })
            except Exception as exc:
                tasks_info.append({
                    "task_id": task_id,
                    "filename": filename,
                    "status": "error",
                    "error": "Не удалось сохранить задание. Повторите загрузку позже.",
                })

        return {"tasks": tasks_info}

    @router.get(
        "/api/v1/documents/bulk-status",
        tags=["Documents"],
        summary="Статусы задач пакетной загрузки",
        dependencies=[Depends(verify_internal_token)],
    )
    async def bulk_status(task_ids: str = Query(default="", description="task_id через запятую; пусто — последние 100 заданий")) -> dict:
        """Текущие статусы задач очереди."""
        if bulk_queue is None:
            raise HTTPException(503, "Очередь пакетной загрузки не инициализирована.")
        ids = [t.strip() for t in task_ids.split(",") if t.strip()]
        results = await resolved(bulk_queue.get_statuses(ids))
        return {
            "tasks": [
                {
                    "task_id": r.task_id,
                    "filename": r.filename,
                    "status": r.status,
                    "document_id": r.document_id,
                    "chunks_count": r.chunks_count,
                    "error": r.error,
                }
                for r in results
            ]
        }

    @router.post('/api/v1/documents/bulk-retry/{task_id}', dependencies=[Depends(verify_internal_token)], tags=['Documents'])
    async def retry_bulk(task_id: str):
        if bulk_queue is None:
            raise HTTPException(503, 'Очередь недоступна')
        if not await bulk_queue.retry(task_id):
            raise HTTPException(409, 'Повторить можно только завершившееся ошибкой задание')
        return {'status': 'queued', 'task_id': task_id}

    @router.get('/api/v1/documents/bulk-review/{task_id}', dependencies=[Depends(verify_internal_token)], tags=['Documents'])
    async def get_bulk_review(task_id: str):
        require_catalog()
        if bulk_queue is None:
            raise HTTPException(503, 'Очередь недоступна')
        result = await bulk_queue.get_review(task_id)
        if result is None:
            raise HTTPException(409, 'Задание не ожидает проверки текста')
        return {'task_id': task_id, 'filename': result['filename'], **result['review']}

    @router.post('/api/v1/documents/bulk-review/{task_id}', dependencies=[Depends(verify_internal_token)], tags=['Documents'])
    async def approve_bulk_review(task_id: str, request: ReviewApprovalRequest, reviewer_id: str = Query(min_length=1, max_length=64)):
        require_catalog()
        if bulk_queue is None:
            raise HTTPException(503, 'Очередь недоступна')
        content = request.content.strip()
        if not content or '\x00' in content or len(content) > settings.pdf_max_chars:
            raise HTTPException(400, 'Текст пуст, содержит недопустимые символы или превышает лимит')
        if not await bulk_queue.approve(task_id, request.review_token, content, reviewer_id):
            raise HTTPException(409, 'Проверка уже завершена, отменена или открыта устаревшая версия')
        return {'task_id': task_id, 'status': 'queued'}

    @router.post('/api/v1/documents/bulk-cancel/{task_id}', dependencies=[Depends(verify_internal_token)], tags=['Documents'])
    async def cancel_bulk(task_id: str):
        require_catalog()
        if bulk_queue is None:
            raise HTTPException(503, 'Очередь недоступна')
        if not await bulk_queue.cancel(task_id):
            raise HTTPException(409, 'Отменить можно только задание в очереди или на проверке')
        return {'task_id': task_id, 'status': 'cancelled'}

    @router.get('/api/v1/documents/versions/{doc_id}', dependencies=[Depends(verify_internal_token)], tags=['Documents'])
    async def document_versions(doc_id: str):
        require_catalog()
        record = await catalog_service.get(doc_id)
        if record is None:
            raise HTTPException(404, 'Документ не найден')
        versions = await catalog_service.versions(record.family_id or record.id)
        return {'items': [_catalog_record_to_dict(item) for item in versions]}

    @router.get('/api/v1/documents/publication/{doc_id}', dependencies=[Depends(verify_internal_token)], tags=['Publication'])
    async def get_publication(doc_id: str):
        require_catalog()
        if knowledge_data is None:
            raise HTTPException(503, 'Публикация недоступна')
        return {'publication': await knowledge_data.publication(doc_id)}

    @router.post('/api/v1/documents/publication/{doc_id}', dependencies=[Depends(verify_internal_token)], tags=['Publication'])
    async def publish_document(doc_id: str, request: PublicationRequest, publisher_id: str = Query(min_length=1, max_length=64)):
        require_catalog()
        if knowledge_data is None:
            raise HTTPException(503, 'Публикация недоступна')
        if not request.title.strip() or not request.content.strip() or '\x00' in request.content:
            raise HTTPException(400, 'Проверьте заголовок и текст публикации')
        try:
            await knowledge_data.publish(doc_id, request.title.strip(), request.content.strip(), publisher_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {'status': 'published'}

    @router.delete('/api/v1/documents/publication/{doc_id}', dependencies=[Depends(verify_internal_token)], tags=['Publication'])
    async def withdraw_publication(doc_id: str):
        require_catalog()
        if knowledge_data is None:
            raise HTTPException(503, 'Публикация недоступна')
        await knowledge_data.withdraw(doc_id)
        return {'status': 'withdrawn'}

    @router.post('/api/v1/consultant/chat', tags=['Consultant'])
    async def public_chat(request: PublicQuestion, x_consultant_key: str | None = Header(default=None, alias='X-Consultant-Key')):
        if not settings.consultant_enabled or not settings.consultant_api_key or consultant_service is None:
            raise HTTPException(503, 'Внешний консультант не настроен')
        if not x_consultant_key or not compare_digest(x_consultant_key.encode(), settings.consultant_api_key.encode()):
            raise HTTPException(401, 'Необходим ключ внешнего API')
        if not request.message.strip():
            raise HTTPException(400, 'Укажите вопрос')
        try:
            return await consultant_service.chat(request.message.strip())
        except TimeoutError:
            raise HTTPException(504, 'Консультант занят. Повторите запрос позже.') from None
        except Exception:
            logger.exception('Ошибка внешнего консультанта')
            raise HTTPException(503, 'Консультант временно недоступен') from None

    def verify_publication_key(x_publication_key: str | None = Header(default=None, alias='X-Publication-Key')):
        expected = settings.consultant_source_key
        if not expected or len(expected) < 32 or expected in {settings.internal_api_key, settings.consultant_api_key}:
            raise HTTPException(503, 'Источник публикаций не настроен')
        if not x_publication_key or not compare_digest(x_publication_key.encode(), expected.encode()):
            raise HTTPException(401, 'Необходим служебный ключ публикаций')

    @router.post('/api/v1/consultant-materials/search', dependencies=[Depends(verify_publication_key)], tags=['Published materials'])
    async def search_publications(request: PublicQuestion):
        require_catalog()
        if knowledge_data is None:
            raise HTTPException(503, 'Источник публикаций недоступен')
        return {'items': await knowledge_data.public_search(request.message)}

    @router.post('/api/v1/consultant-materials/validate', dependencies=[Depends(verify_publication_key)], tags=['Published materials'])
    async def validate_publications(request: PublishedIdsRequest):
        require_catalog()
        if knowledge_data is None:
            raise HTTPException(503, 'Источник публикаций недоступен')
        return {'publication_ids': sorted(await knowledge_data.public_ids([str(identifier) for identifier in request.publication_ids]))}

    return router


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _catalog_record_to_dict(record: Any) -> dict:
    """Сериализовать DocumentRecord в dict для JSON-ответа."""
    return {
        "id": record.id,
        "title": record.title,
        "mime_type": record.mime_type,
        "file_size": record.file_size,
        "sha256": record.sha256,
        "owner_id": record.owner_id,
        "version": record.version,
        "family_id": record.family_id,
        "is_active": record.is_active,
        "parent_id": record.parent_id,
        "document_kind": record.document_kind,
        "original_filename": record.original_filename,
        "status": record.status,
        "is_public": record.is_public,
        "allowed_roles": record.allowed_roles,
        "allowed_departments": record.allowed_departments,
        "has_original": record.storage_key is not None,
        "chunks_count": record.chunks_count,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _ext_for_mime(mime: str) -> str:
    """Получить расширение файла по MIME-типу."""
    ext = {
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "text/markdown": ".md",
    }.get(mime)
    if ext:
        return ext
    import mimetypes
    guessed = mimetypes.guess_extension(mime)
    return guessed or ""
