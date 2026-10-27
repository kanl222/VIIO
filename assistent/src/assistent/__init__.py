"""Composition Root микросервиса assistent.

Является единственным местом сборки графа зависимостей (DI), инициализации FastAPI
приложения и точкой входа запуска микросервиса. Поддерживает LM Studio и Ollama,
а также асинхронное персистентное сохранение сессий в PostgreSQL через AsyncPostgresSaver.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import asyncio
from typing import AsyncIterator
from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from psycopg_pool import AsyncConnectionPool
from qdrant_client import QdrantClient

from assistent.config import Settings
from assistent.interfaces._protocols import IAgentService, IDocumentService
from assistent.models import create_chat_model, create_coding_model, create_embeddings
from assistent.router import create_api_router
from assistent.services.agent_service import AgentService
from assistent.services.persistent_bulk_queue import PersistentBulkQueue
from assistent.services.document_ingestion import DocumentIngestion
from assistent.services.database_agent import DatabaseAgentService
from assistent.services.document_catalog import DocumentCatalogService
from assistent.services.file_store import LocalFileStore
from assistent.services.knowledge_data import KnowledgeData
from assistent.services.publication_index import PublicationIndex
from assistent.services.enhanced_retrieval import EnhancedRetrieval
from assistent.services.personal_memory import PersonalFacts, MemoryAgent
from assistent.services.public_consultant import ConsultantProxy
from assistent.tools.postgres_tool import create_postgres_readonly_tool
from assistent.services.coding_agent import (
    CODING_SYSTEM_PROMPT, DELEGATION_PROMPT, CodingAgentService,
    MultiAgentService, create_coding_delegation_tool,
)
from assistent.services.document_service import DocumentService
from assistent.services.graph_builder import DEFAULT_SYSTEM_PROMPT, build_agent_graph
from assistent.tools.search_tool import create_knowledge_search_tool
from assistent.utils.postgres_saver import init_async_postgres_saver
from assistent.utils.vector_store import QdrantVectorStoreAdapter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Управление жизненным циклом асинхронных ресурсов приложения FastAPI.

    При старте сервера открывает пул соединений AsyncConnectionPool и регистрирует
    AsyncPostgresSaver в графе агента для персистентности сессий. При остановке
    сервера гарантирует корректное закрытие пула соединений.
    """
    pool: AsyncConnectionPool | None = None
    agent_svc = getattr(app.state, "agent_service", None)
    enable_postgres = getattr(app.state, "enable_postgres_checkpointer", False)

    if agent_svc is not None and enable_postgres and hasattr(agent_svc, "set_checkpointer"):
        settings: Settings = app.state.settings
        conn_string = (
            f"postgresql://{settings.postgres_user}:{settings.postgres_password}@"
            f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
        )
        try:
            saver, pool = await init_async_postgres_saver(conn_string)
            agent_svc.set_checkpointer(saver)
            app.state.pool = pool
            logger.info(
                "Успешно инициализирован AsyncPostgresSaver с пулом соединений PostgreSQL (%s) для agent_db",
                settings.postgres_host,
            )
        except Exception as exc:
            logger.warning(
                "Не удалось подключиться к PostgreSQL (%s) для AsyncPostgresSaver, используется MemorySaver: %s",
                settings.postgres_host,
                type(exc).__name__,
            )

    remote_documents = getattr(app.state, 'remote_documents', False)
    catalog_svc = None if remote_documents else getattr(app.state, "catalog_service", None)
    bulk_q = getattr(app.state, "bulk_queue", None)
    catalog_pool = None
    if catalog_svc is not None:
        settings = app.state.settings
        try:
            from psycopg.conninfo import make_conninfo
            from psycopg.rows import dict_row
            catalog_pool = AsyncConnectionPool(conninfo=make_conninfo(
                host=settings.postgres_host, port=settings.postgres_port, dbname=settings.postgres_db,
                user=settings.postgres_user, password=settings.postgres_password),
                min_size=1, max_size=settings.bulk_max_workers + 4,
                kwargs={"row_factory": dict_row}, open=False)
            await catalog_pool.open(wait=True, timeout=5)
            catalog_svc.set_pool(catalog_pool)
            await catalog_svc.setup()
            if bulk_q is not None:
                await bulk_q.setup()
            documents = app.state.document_service
            data = KnowledgeData(catalog_svc)
            if isinstance(documents, DocumentService) and settings.service_role != 'documents':
                import anyio.to_thread
                metadata = await anyio.to_thread.run_sync(documents.existing_document_metadata)
                for item in metadata:
                    if await catalog_svc.get(item["document_id"]) is None:
                        await catalog_svc.create(doc_id=item["document_id"], title=item.get("title", "Без названия"),
                            chunks_count=item["chunks_count"], owner_id=item.get("owner_id"),
                            is_public=item.get("is_public", False), allowed_roles=item.get("allowed_roles", []),
                            allowed_departments=item.get("allowed_departments", []))
                    if item.get('search_text') and not await data.has_text(item['document_id']):
                        await data.save_text(item['document_id'], item['search_text'], item.get('filename', ''), item.get('title', ''))
            ingestion = getattr(app.state, 'ingestion_service', None)
            if isinstance(ingestion, DocumentIngestion) and settings.service_role != 'documents':
                await ingestion.recover_versions()
            if bulk_q is not None and settings.service_role != 'documents':
                bulk_q.start()
        except Exception:
            logger.exception("Каталог документов недоступен; загрузка отключена")
            catalog_svc.set_pool(None)
            if catalog_pool is not None:
                await catalog_pool.close()
                catalog_pool = None

    async def reconcile_publications():
        data = getattr(app.state, 'knowledge_data', None)
        while True:
            try:
                if data is not None and catalog_svc.pool is not None:
                    slots = getattr(app.state, 'processing_slots', None)
                    if slots is None:
                        await data.sync_public_index()
                    else:
                        async with slots:
                            await data.sync_public_index()
            except Exception:
                logger.exception('Public index reconciliation failed; will retry')
            await asyncio.sleep(30)

    public_task = asyncio.create_task(reconcile_publications()) if catalog_svc and app.state.settings.service_role != 'documents' else None
    facts_pool = None
    if remote_documents and app.state.catalog_service is not None:
        from psycopg.conninfo import make_conninfo
        from psycopg.rows import dict_row
        settings = app.state.settings
        try:
            facts_pool = AsyncConnectionPool(make_conninfo(host=settings.postgres_host,
                port=settings.postgres_port, dbname=settings.postgres_db, user=settings.postgres_user,
                password=settings.postgres_password), kwargs={'row_factory': dict_row}, open=False)
            await facts_pool.open(wait=True, timeout=5)
            app.state.catalog_service.set_pool(facts_pool)
            await PersonalFacts(app.state.catalog_service).setup()
        except Exception:
            logger.exception('Personal facts unavailable')
            app.state.catalog_service.set_pool(None)
    yield

    if public_task:
        public_task.cancel()
        try:
            await public_task
        except asyncio.CancelledError:
            pass
    if facts_pool:
        await facts_pool.close()

    # Остановка воркеров
    if bulk_q is not None:
        await bulk_q.stop()

    # Закрытие пулов
    if catalog_pool is not None:
        await catalog_pool.close()

    pool_to_close: AsyncConnectionPool | None = getattr(app.state, "pool", None) or pool
    if pool_to_close is not None:
        try:
            await pool_to_close.close()
            logger.info("Пул соединений AsyncConnectionPool успешно закрыт")
        except Exception as exc:
            logger.warning("Ошибка при закрытии пула AsyncConnectionPool: %s", exc)


def create_app(
    settings: Settings | None = None,
    agent_service: IAgentService | None = None,
    document_service: IDocumentService | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> FastAPI:
    """Фабрика создания и конфигурирования FastAPI приложения с инжекцией зависимостей.

    Args:
        settings: Опциональный объект конфигурации Settings. Если None, загружается из окружения.
        agent_service: Опциональная реализация IAgentService (для подмены в тестах).
        document_service: Опциональная реализация IDocumentService (для подмены в тестах).
        checkpointer: Опциональный чекпоинтер сессий (для явной подмены в тестах).

    Returns:
        Сконфигурированный экземпляр приложения FastAPI с подключенными роутами и lifespan.
    """
    app_settings = settings or Settings()
    processing_service = app_settings.service_role != 'assistent'
    remote_documents = not processing_service and document_service is None
    if remote_documents and not app_settings.documents_url:
        raise ValueError('Для assistent нужен DOCUMENTS_URL; локальная обработка документов отключена')
    if processing_service and agent_service is None:
        agent_service = object()  # Document processes never construct chat/coder models.
    enable_postgres = checkpointer is None and not processing_service

    # ------------------------------------------------------------------
    # Файловое хранилище и каталог
    # ------------------------------------------------------------------
    file_store: LocalFileStore | None = None
    catalog_service: DocumentCatalogService | None = None
    bulk_queue: PersistentBulkQueue | None = None
    data = None
    consultant = None

    if app_settings.documents_catalog_enabled:
        if not remote_documents:
            file_store = LocalFileStore(app_settings.documents_storage_path)
        # The catalog pool is opened independently of checkpointing.
        catalog_service = DocumentCatalogService(None)
        if not remote_documents:
            data = KnowledgeData(catalog_service)

    if document_service is None or agent_service is None:
        logger.info(
            "Инициализация инфраструктурных компонентов: Qdrant (%s), Провайдер LLM (%s)",
            app_settings.qdrant_url,
            app_settings.llm_provider,
        )
        vector_store = None
        if not remote_documents:
            qdrant_client = QdrantClient(url=app_settings.qdrant_url)
            embeddings = create_embeddings(app_settings)
            vector_store = QdrantVectorStoreAdapter(qdrant_client, embeddings,
                collection_name=app_settings.qdrant_collection_name)
        if data is not None:
            data.public_index = PublicationIndex(QdrantVectorStoreAdapter(
                qdrant_client, embeddings, app_settings.qdrant_public_collection_name))

        if document_service is None and not remote_documents:
            document_service = DocumentService(
                vector_store=vector_store,
                chunk_size=app_settings.rag_chunk_size,
                chunk_overlap=app_settings.rag_chunk_overlap,
                chunk_unit=app_settings.rag_chunk_unit,
            )

        if agent_service is None:
            llm = create_chat_model(app_settings)

            from assistent.services.documents_client import create_remote_search_tool
            search_tool = create_remote_search_tool(app_settings, 5) if remote_documents else create_knowledge_search_tool(vector_store=vector_store)
            enhanced = EnhancedRetrieval(vector_store, data) if data else None
            if enhanced:
                search_tool = enhanced.tool(search_tool, 5)

            # Изначально используем переданный чекпоинтер или MemorySaver.
            # Если база PostgreSQL доступна, AsyncPostgresSaver подключится в lifespan.
            initial_checkpointer = checkpointer or MemorySaver()

            active_tools = [search_tool]
            coding_service = None
            if app_settings.coding_agent_enabled:
                coding_llm = create_coding_model(app_settings)
                coding_graph = build_agent_graph(
                    llm=coding_llm,
                    tools=[],
                    checkpointer=initial_checkpointer,
                    system_prompt=CODING_SYSTEM_PROMPT,
                    summary_after=app_settings.conversation_summary_after if app_settings.conversation_summary_enabled else 0,
                    history_max_chars=app_settings.conversation_history_max_chars,
                )
                coding_service = CodingAgentService(
                    coding_graph, app_settings.coding_allowed_roles,
                    knowledge_search=create_remote_search_tool(app_settings, 3) if remote_documents else enhanced.tool(create_knowledge_search_tool(vector_store=vector_store, k=3), 3) if enhanced else create_knowledge_search_tool(vector_store=vector_store, k=2),
                    context_max_chars=app_settings.coding_context_max_chars,
                )
                active_tools.append(create_coding_delegation_tool(coding_service))

            graph = build_agent_graph(
                llm=llm,
                tools=active_tools,
                checkpointer=initial_checkpointer,
                system_prompt=DEFAULT_SYSTEM_PROMPT + (DELEGATION_PROMPT if coding_service else ""),
                context_max_chars=app_settings.rag_context_max_chars,
                model_first_tools=True,
                summary_after=app_settings.conversation_summary_after if app_settings.conversation_summary_enabled else 0,
                history_max_chars=app_settings.conversation_history_max_chars,
            )
            agent_service = AgentService(graph=graph)
            if coding_service:
                agent_service = MultiAgentService(agent_service, coding_service, model_first=True)
            agent_service = DatabaseAgentService(
                agent_service, llm, create_postgres_readonly_tool(app_settings),
                enabled=app_settings.pgsql_enabled,
            )
            if catalog_service:
                agent_service = MemoryAgent(agent_service, PersonalFacts(catalog_service))
            if app_settings.consultant_enabled:
                if (not data and not remote_documents) or not app_settings.consultant_api_key or len(app_settings.consultant_api_key) < 32 or app_settings.consultant_api_key == app_settings.internal_api_key:
                    raise ValueError('Для внешнего консультанта нужны каталог и отдельный CONSULTANT_API_KEY длиной от 32 символов.')
                consultant = ConsultantProxy(app_settings.consultant_url, app_settings.consultant_api_key)

    ingestion = None
    processing_slots = asyncio.Semaphore(app_settings.bulk_max_workers) if app_settings.service_role == 'document-worker' else None
    if file_store is not None and catalog_service is not None:
        ingestion = DocumentIngestion(document_service, catalog_service, file_store, app_settings, knowledge_data=data)
        bulk_queue = PersistentBulkQueue(ingestion, catalog_service, file_store,
                                         max_workers=app_settings.bulk_max_workers, processing_slots=processing_slots)

    app = FastAPI(
        title="assistent",
        description="LangGraph RAG Agent REST API с Qdrant и поддержкой LM Studio / Ollama",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.settings = app_settings
    app.state.agent_service = agent_service
    app.state.enable_postgres_checkpointer = enable_postgres
    app.state.pool = None
    app.state.document_service = document_service
    app.state.ingestion_service = ingestion
    app.state.catalog_service = catalog_service
    app.state.bulk_queue = bulk_queue
    app.state.knowledge_data = data
    app.state.remote_documents = remote_documents
    app.state.vector_store = locals().get('vector_store')
    app.state.processing_slots = processing_slots

    api_router = create_api_router(
        agent_service=agent_service,
        document_service=document_service,
        settings=app_settings,
        catalog_service=catalog_service,
        file_store=file_store,
        bulk_queue=bulk_queue,
        ingestion_service=ingestion,
        knowledge_data=data,
        consultant_service=consultant,
    )
    from assistent.service_profiles import configure_service
    configure_service(app, api_router)
    app.include_router(api_router)

    return app


def main() -> None:
    """Точка входа запуска сервиса через консольную команду assistent."""
    from assistent.runner import run_app

    run_app()
