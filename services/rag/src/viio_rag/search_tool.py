"""Инструмент семантического поиска в базе знаний для агента LangGraph с разграничением прав доступа."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from html import escape
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from qdrant_client import models

from viio_rag.interfaces import IVectorStore

logger = logging.getLogger(__name__)

_CONTEXT_TAG_RE = re.compile(r"</?context\b[^>]*>", re.IGNORECASE)


def build_access_filter(user_context: dict[str, Any] | None) -> models.Filter | None:
    """Формирование Qdrant payload-фильтра на основе контекста безопасности пользователя.

    Args:
        user_context: Контекст пользователя с ключами 'user_id', 'role', 'departments'.

    Returns:
        Объект models.Filter для Qdrant или None, если пользователь обладает полным доступом (admin).
    """
    if not user_context:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.is_public",
                    match=models.MatchValue(value=True),
                )
            ],
            must_not=[
                models.FieldCondition(
                    key="metadata.kind",
                    match=models.MatchValue(value="code"),
                )
            ],
        )

    user_id = user_context.get("user_id")
    raw_role = user_context.get("role")
    role = str(raw_role).strip().lower() if raw_role else None

    if role == "admin":
        logger.debug("Пользователь с ролью 'admin': поиск без ограничений прав доступа")
        return None

    raw_departments = user_context.get("departments")
    if isinstance(raw_departments, list):
        departments = [str(d).strip() for d in raw_departments if d]
    elif raw_departments:
        departments = [str(raw_departments).strip()]
    else:
        departments = []

    should_conditions: list[models.Condition] = [
        # 1. Общедоступные документы компании
        models.FieldCondition(
            key="metadata.is_public",
            match=models.MatchValue(value=True),
        ),
    ]

    # 2. Документы, разрешенные для роли пользователя
    if role:
        allowed_roles = [role, "employee"] if role == "programmer" else [role]
        should_conditions.append(
            models.FieldCondition(
                key="metadata.allowed_roles",
                match=models.MatchAny(any=allowed_roles),
            )
        )

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

    excluded: list[models.Condition] = (
        []
        if role in {"admin", "programmer"}
        else [
            models.FieldCondition(
                key="metadata.kind",
                match=models.MatchValue(value="code"),
            )
        ]
    )

    return models.Filter(should=should_conditions, must_not=excluded)


def create_knowledge_search_tool(vector_store: IVectorStore, k: int = 5) -> BaseTool:
    """Создание инструмента LangChain для поиска по базе знаний с проверкой прав доступа.

    Args:
        vector_store: Экземпляр адаптера векторного хранилища (IVectorStore).
        k: Количество возвращаемых релевантных фрагментов (по умолчанию 5).

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
        if not query or not query.strip():
            return "Поисковый запрос не может быть пустым."

        user_context: dict[str, Any] | None = None
        if isinstance(config, dict):
            configurable = config.get("configurable") or {}
            user_context = configurable.get("user_context")

        logger.info(
            "Вызов search_knowledge_base: запрос='%s', user_id=%s, role=%s",
            query,
            user_context.get("user_id") if isinstance(user_context, dict) else None,
            user_context.get("role") if isinstance(user_context, dict) else None,
        )

        access_filter = build_access_filter(user_context)

        try:
            docs = vector_store.similarity_search(query=query, k=k, filter=access_filter)
        except Exception as exc:
            logger.exception("Ошибка при выполнении поиска в базе знаний")
            return f"Ошибка при обращении к базе знаний: {exc}"

        return format_documents(docs)

    return search_knowledge_base


def format_documents(docs: Sequence[Any]) -> str:
    """Форматирование найденных документов в XML-блоки <context>."""
    if not docs:
        return "Ничего не найдено в базе знаний в рамках ваших прав доступа."

    formatted_parts: list[str] = []
    for idx, doc in enumerate(docs, 1):
        meta: dict[str, Any] = getattr(doc, "metadata", None) or {}
        title = meta.get("title", "Без названия")
        doc_id = meta.get("document_id", "unknown")
        content = str(getattr(doc, "page_content", doc))

        window = meta.get("window_context")
        if window and len(str(window)) > len(content):
            content = str(window)

        if meta.get("kind") == "code":
            filename = meta.get("filename", title)
            line_start = meta.get("line_start", 1)
            line_end = meta.get("line_end", 1)
            content = f"Файл: {filename}, строки {line_start}–{line_end}\n{content}"

        section_path = meta.get("section_path")
        breadcrumb = ""
        if isinstance(section_path, (list, tuple)) and section_path:
            breadcrumb = " > ".join(str(p).strip() for p in section_path if str(p).strip())
        elif isinstance(section_path, str) and section_path.strip():
            breadcrumb = section_path.strip()

        if breadcrumb and not content.lstrip().startswith(("#", "[Раздел:")):
            content = f"[Раздел: {breadcrumb}]\n{content}"

        # Защита от prompt injection: экранирование любых вариаций тега <context>
        safe_content = _CONTEXT_TAG_RE.sub(
            lambda m: escape(m.group(0)), content
        )
        section_attr = f' section="{escape(breadcrumb, quote=True)}"' if breadcrumb else ""
        formatted_parts.append(
            f'<context source="{escape(str(title), quote=True)}" '
            f'doc_id="{escape(str(doc_id), quote=True)}" chunk="{idx}"{section_attr}>\n'
            f"{safe_content}\n"
            f"</context>"
        )

    return "\n\n".join(formatted_parts)