"""Инструмент семантического поиска в базе знаний для агента LangGraph."""

from __future__ import annotations

import logging
from langchain_core.tools import BaseTool, tool

from apiagent.interfaces._protocols import IVectorStore

logger = logging.getLogger(__name__)


def create_knowledge_search_tool(vector_store: IVectorStore, k: int = 4) -> BaseTool:
    """Создание инструмента LangChain для поиска по базе знаний.

    Args:
        vector_store: Экземпляр адаптера векторного хранилища (IVectorStore).
        k: Количество возвращаемых релевантных фрагментов (по умолчанию 4).

    Returns:
        Экземпляр BaseTool с именем 'search_knowledge_base'.
    """

    @tool("search_knowledge_base")
    def search_knowledge_base(query: str) -> str:
        """Поиск информации в базе знаний компании по текстовому запросу.

        Используйте этот инструмент для поиска регламентов, инструкций, процедур
        и документации компании.

        Args:
            query: Поисковый запрос для нахождения информации в документах.

        Returns:
            Текст с найденными релевантными фрагментами или сообщение о том,
            что ничего не найдено.
        """
        logger.info("Вызов инструмента search_knowledge_base с запросом: '%s'", query)
        try:
            docs = vector_store.similarity_search(query=query, k=k)
        except Exception as exc:
            logger.error("Ошибка при выполнении поиска в базе знаний: %s", exc)
            return f"Ошибка при обращении к базе знаний: {exc}"

        if not docs:
            return "Ничего не найдено в базе знаний."

        formatted_parts: list[str] = []
        for idx, doc in enumerate(docs, 1):
            title = "Без названия"
            if hasattr(doc, "metadata") and doc.metadata:
                title = doc.metadata.get("title", "Без названия")
            content = getattr(doc, "page_content", str(doc))
            formatted_parts.append(f"[Фрагмент {idx}] (Документ: {title}):\n{content}")

        return "\n\n".join(formatted_parts)

    return search_knowledge_base
