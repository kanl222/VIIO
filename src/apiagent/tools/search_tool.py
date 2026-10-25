"""Инструмент семантического поиска в базе знаний для агента LangGraph с разграничением прав доступа."""

from __future__ import annotations

import logging
from typing import Any
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from qdrant_client import models

from apiagent.interfaces._protocols import IVectorStore

logger = logging.getLogger(__name__)


def build_access_filter(user_context: dict[str, Any] | None) -> Any | None:
    """Формирование Qdrant payload-фильтра на основе контекста безопасности пользователя.

    Args:
        user_context: Контекст пользователя с ключами 'user_id', 'role', 'departments'.

    Returns:
        Объект models.Filter для Qdrant или None, если пользователь обладает полным доступом (admin).
    """
    if not user_context:
        # Для неаутентифицированных запросов возвращаются только публичные документы компании
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.is_public",
                    match=models.MatchValue(value=True),
                )
            ]
        )

    user_id = user_context.get("user_id")
    role = str(user_context.get("role", "employee")).lower()
    raw_departments = user_context.get("departments", ["general"])
    departments = (
        raw_departments
        if isinstance(raw_departments, list)
        else [str(raw_departments)]
    )

    # Администратор системы обладает полным доступом ко всей базе знаний
    if role == "admin":
        logger.debug("Пользователь с ролью 'admin': поиск без ограничений прав доступа")
        return None

    should_conditions: list[Any] = [
        # 1. Общедоступные документы компании
        models.FieldCondition(
            key="metadata.is_public",
            match=models.MatchValue(value=True),
        ),
        # 2. Документы, разрешенные для роли пользователя
        models.FieldCondition(
            key="metadata.allowed_roles",
            match=models.MatchAny(any=[role, "employee"] if role == "programmer" else [role]),
        ),
    ]

    # 3. Документы, разрешенные для подразделений/отделов пользователя
    if departments:
        should_conditions.append(
            models.FieldCondition(
                key="metadata.allowed_departments",
                match=models.MatchAny(any=departments),
            )
        )

    # 4. Личные документы пользователя
    if user_id:
        should_conditions.append(
            models.FieldCondition(
                key="metadata.owner_id",
                match=models.MatchValue(value=str(user_id)),
            )
        )

    return models.Filter(should=should_conditions)


def create_knowledge_search_tool(vector_store: IVectorStore, k: int = 5) -> BaseTool:
    """Создание инструмента LangChain для поиска по базе знаний с проверкой прав доступа.

    Args:
        vector_store: Экземпляр адаптера векторного хранилища (IVectorStore).
        k: Количество возвращаемых релевантных фрагментов (по умолчанию 4).

    Returns:
        Экземпляр BaseTool с именем 'search_knowledge_base'.
    """

    @tool("search_knowledge_base")
    def search_knowledge_base(query: str, config: RunnableConfig) -> str:
        """Поиск информации в базе знаний компании по текстовому запросу.

        Используйте этот инструмент для поиска регламентов, инструкций, процедур
        и документации компании.

        Args:
            query: Поисковый запрос для нахождения информации в документах.
            config: Автоматически инжектируемая конфигурация среды исполнения LangGraph.

        Returns:
            Текст с найденными релевантными фрагментами в тегах <context> или сообщение о том,
            что ничего не найдено.
        """
        user_context: dict[str, Any] | None = None
        if config and isinstance(config, dict):
            configurable = config.get("configurable", {})
            user_context = configurable.get("user_context")

        logger.info(
            "Вызов search_knowledge_base: запрос='%s', user_context=%s",
            query,
            user_context,
        )

        access_filter = build_access_filter(user_context)

        try:
            docs = vector_store.similarity_search(query=query, k=k, filter=access_filter)
        except Exception as exc:
            logger.error("Ошибка при выполнении поиска в базе знаний: %s", exc)
            return f"Ошибка при обращении к базе знаний: {exc}"

        if not docs:
            return "Ничего не найдено в базе знаний в рамках ваших прав доступа."

        formatted_parts: list[str] = []
        for idx, doc in enumerate(docs, 1):
            title = "Без названия"
            doc_id = "unknown"
            if hasattr(doc, "metadata") and doc.metadata:
                title = doc.metadata.get("title", "Без названия")
                doc_id = doc.metadata.get("document_id", "unknown")
            content = getattr(doc, "page_content", str(doc))

            # Защита от prompt injection: экранирование закрывающего тега
            safe_content = content.replace("</context>", "&lt;/context&gt;")
            formatted_parts.append(
                f'<context source="{title}" doc_id="{doc_id}" chunk="{idx}">\n'
                f"{safe_content}\n"
                f"</context>"
            )

        return "\n\n".join(formatted_parts)

    return search_knowledge_base
