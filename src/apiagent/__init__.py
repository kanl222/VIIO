"""Composition Root микросервиса APIAgent.

Является единственным местом сборки графа зависимостей (DI), инициализации FastAPI
приложения и точкой входа запуска микросервиса.
"""

from __future__ import annotations

import logging
from fastapi import FastAPI
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langgraph.checkpoint.memory import MemorySaver
from qdrant_client import QdrantClient

from apiagent.config import Settings
from apiagent.interfaces._protocols import IAgentService, IDocumentService
from apiagent.router import create_api_router
from apiagent.services.agent_service import AgentService
from apiagent.services.document_service import DocumentService
from apiagent.services.graph_builder import build_agent_graph
from apiagent.tools.search_tool import create_knowledge_search_tool
from apiagent.utils.vector_store import QdrantVectorStoreAdapter

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    agent_service: IAgentService | None = None,
    document_service: IDocumentService | None = None,
) -> FastAPI:
    """Фабрика создания и конфигурирования FastAPI приложения с инжекцией зависимостей.

    Args:
        settings: Опциональный объект конфигурации Settings. Если None, загружается из окружения.
        agent_service: Опциональная реализация IAgentService (для подмены в тестах).
        document_service: Опциональная реализация IDocumentService (для подмены в тестах).

    Returns:
        Сконфигурированный экземпляр приложения FastAPI с подключенными роутами.
    """
    app_settings = settings or Settings()

    if document_service is None or agent_service is None:
        logger.info(
            "Инициализация инфраструктурных компонентов: Qdrant (%s), Ollama (%s)",
            app_settings.qdrant_url,
            app_settings.ollama_base_url,
        )
        qdrant_client = QdrantClient(url=app_settings.qdrant_url)
        embeddings = OllamaEmbeddings(
            base_url=app_settings.ollama_base_url,
            model=app_settings.ollama_embedding_model,
        )
        vector_store = QdrantVectorStoreAdapter(
            client=qdrant_client,
            embeddings=embeddings,
            collection_name=app_settings.qdrant_collection_name,
        )

        if document_service is None:
            document_service = DocumentService(vector_store=vector_store)

        if agent_service is None:
            llm = ChatOllama(
                base_url=app_settings.ollama_base_url,
                model=app_settings.ollama_model,
            )
            search_tool = create_knowledge_search_tool(vector_store=vector_store)
            checkpointer = MemorySaver()
            graph = build_agent_graph(
                llm=llm,
                tools=[search_tool],
                checkpointer=checkpointer,
            )
            agent_service = AgentService(graph=graph)

    app = FastAPI(
        title="APIAgent",
        description="LangGraph RAG Agent REST API с Qdrant и Docker",
        version="0.1.0",
    )

    api_router = create_api_router(
        agent_service=agent_service,
        document_service=document_service,
        settings=app_settings,
    )
    app.include_router(api_router)

    return app


def main() -> None:
    """Точка входа запуска сервиса через консольную команду apiagent."""
    from apiagent.runner import run_app

    run_app()
