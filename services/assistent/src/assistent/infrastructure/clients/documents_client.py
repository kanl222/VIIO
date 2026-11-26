"""Async knowledge client; assistant never indexes documents locally."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from hmac import compare_digest
from typing import Any

import httpx
from fastapi import HTTPException, Request
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from assistent.application.answer_sources import readable_reference, sources_from_reference
from assistent.observability.diagnostics import enabled, record, trace_headers

logger = logging.getLogger(__name__)


def require_internal(request: Request, settings: Any) -> None:
    """Проверка межсервисного токена в заголовках входящего запроса."""
    token = request.headers.get("X-Internal-Token", "")
    if not settings.internal_api_key:
        raise HTTPException(503, "Межсервисный ключ не настроен на стороне сервера")
    if not compare_digest(token.encode(), settings.internal_api_key.encode()):
        raise HTTPException(401, "Необходим валидный межсервисный ключ")


def create_remote_search_tool(settings: Any, k: int = 5, *, tool_name: str = "search_knowledge_base") -> Any:
    """Создание инструмента удаленного гибридного поиска по базе знаний."""

    @tool(tool_name, response_format="content_and_artifact")
    async def search_knowledge_base(
        query: str,
        config: RunnableConfig,
        document_ids: Sequence[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Единый гибридный поиск по базе знаний (GraphRAG):
- Онтология аптечного склада: расшифровка аббревиатур (ИСЛО, МДЛП, ЕГИСЗ), бизнес-процессы, шаги алгоритмов, температурные режимы хранения, информационные системы и нормативные акты.
- Содержимое регламентирующих документов, инструкций и руководств.

        Используйте этот инструмент по умолчанию для любых вопросов о терминах, регламентах, алгоритмах и инструкциях.
        document_ids: Необязательный массив точных doc_id для фильтрации, например ["doc-123"].
        """
        config_dict = config or {}
        context = config_dict.get("configurable", {}).get("user_context") or {}
        # Очищаем контекст от лишних ключей, оставляя только атрибуты безопасности
        safe_context = {
            key: context[key]
            for key in ("user_id", "role", "departments")
            if key in context
        }

        start = time.monotonic()
        token = (
            settings.documents_read_key
            or (settings.internal_api_key if not getattr(settings, "secure_runtime", False) else "")
            or ""
        )

        url = f"{settings.documents_url.rstrip('/')}/api/v1/knowledge/search"
        payload_data = {
            "query": query,
            "k": k,
            "user_context": safe_context or None,
        }
        if document_ids is not None:
            payload_data["document_ids"] = list(document_ids)

        from assistent.observability.telemetry import is_phoenix_active, record_rag_retrieval_telemetry
        if enabled() or is_phoenix_active():
            payload_data["diagnostics"] = True

        rag_timeout = float(getattr(settings, "rag_timeout_seconds", 60))
        try:
            async with httpx.AsyncClient(timeout=rag_timeout, trust_env=False) as client:
                response = await client.post(
                    url,
                    headers={
                        "X-Internal-Token": token,
                        **trace_headers(),
                    },
                    json=payload_data,
                )
                response.raise_for_status()
                payload = response.json()
                text = payload.get("context", "")

                if not isinstance(text, str) or len(text) > 100000:
                    raise ValueError("Получен некорректный ответ от службы документов")

                sources = sources_from_reference(text)
                max_chars = getattr(settings, "rag_context_max_chars", None)
                if max_chars and tool_name == "search_knowledge_base":
                    text = readable_reference(text, max_chars=max_chars, include_ids=True)

                duration_ms = round((time.monotonic() - start) * 1000)
                diag = payload.get("diagnostics", {})
                record(
                    "retrieval",
                    query=query,
                    duration_ms=duration_ms,
                    context=text,
                    **diag,
                )
                record_rag_retrieval_telemetry(
                    query=query,
                    context=text,
                    diagnostics=diag,
                    document_ids=document_ids,
                    duration_ms=duration_ms,
                    status="ok",
                )
                return text, {"sources": sources, "fragments": payload.get('fragments', [])}
        except httpx.HTTPStatusError as exc:
            duration_ms = round((time.monotonic() - start) * 1000)
            logger.warning("Ошибка HTTP при обращении к базе знаний: %s (код %s)", exc, exc.response.status_code)
            record("retrieval", query=query, status="error", duration_ms=duration_ms)
            record_rag_retrieval_telemetry(
                query=query,
                context="",
                document_ids=document_ids,
                duration_ms=duration_ms,
                status="error",
            )
            return "Ошибка при обращении к базе знаний. Повторите попытку позже.", {}
        except (httpx.HTTPError, ValueError, KeyError):
            duration_ms = round((time.monotonic() - start) * 1000)
            logger.exception("Критическая ошибка при выполнении поиска в базе знаний")
            record("retrieval", query=query, status="error", duration_ms=duration_ms)
            record_rag_retrieval_telemetry(
                query=query,
                context="",
                document_ids=document_ids,
                duration_ms=duration_ms,
                status="error",
            )
            return "Ошибка при обращении к базе знаний. Повторите попытку позже.", {}

    return search_knowledge_base


def create_document_list_tool(settings: Any) -> Any:
    """Создание инструмента каталога документов."""

    @tool("list_documents", response_format="content_and_artifact")
    async def list_documents(
        config: RunnableConfig,
        query: str = "",
        tags: Sequence[str] | None = None,
        offset: int = 0,
    ) -> tuple[str, dict[str, Any]]:
        """Каталог доступных документов, без чтения содержимого.

        Для вопросов о доступных документах, инструкциях и руководствах, поиска названий и ссылок.
        query: Подстрока названия или описания, пустая строка для полного перечня.
        tags: Массив требуемых тегов, например ["инструкция", "ис ло"]. Необязательный аргумент.
        offset: Начало страницы. Продолжение по next_offset только по просьбе пользователя.
        Возвращает одну страницу, total, next_offset и untagged_count. Один вызов за ход.
        """
        config_dict = config or {}
        context = config_dict.get("configurable", {}).get("user_context") or {}
        safe_context = {
            key: context[key]
            for key in ("user_id", "role", "departments")
            if key in context
        }

        token = (
            settings.documents_read_key
            or (settings.internal_api_key if not getattr(settings, "secure_runtime", False) else "")
            or ""
        )
        url = f"{settings.documents_url.rstrip('/')}/api/v1/knowledge/documents"

        rag_timeout = float(getattr(settings, "rag_timeout_seconds", 60))
        try:
            async with httpx.AsyncClient(timeout=rag_timeout, trust_env=False) as client:
                response = await client.post(
                    url,
                    headers={
                        "X-Internal-Token": token,
                        **trace_headers(),
                    },
                    json={
                        "query": query,
                        "tags": list(tags) if tags else [],
                        "offset": offset,
                        "limit": 20,
                        "user_context": safe_context or None,
                    },
                )
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError):
            logger.exception("Не удалось получить каталог документов")
            record("document_catalog", status="error")
            return "Каталог недоступен. Не удалось проверить список документов. Повторите запрос позже.", {"sources": []}

        items = result.get("items", [])
        sources = [
            {"title": item.get("title", "Без названия"), "document_id": item.get("document_id", "unknown")}
            for item in items
        ]
        record("document_catalog", total=result.get("total", 0), count=len(items), offset=offset)
        return json.dumps(result, ensure_ascii=False), {"sources": sources}

    return list_documents


async def access_revision(settings: Any) -> str:
    """Проверка ревизии актуальности прав и доступов к знаниям."""
    token = settings.documents_read_key or ""
    url = f"{settings.documents_url.rstrip('/')}/api/v1/knowledge/access-revision"

    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.get(
                url,
                headers={"X-Internal-Token": token},
            )
            response.raise_for_status()
            data = response.json()
            revision = data.get("revision")
            if not isinstance(revision, str) or len(revision) != 32:
                raise ValueError("Некорректный формат ревизии доступа")
            return revision
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        logger.warning("Ошибка проверки ревизии доступа к знаниям: %s", exc)
        raise HTTPException(503, "Не удалось проверить актуальность доступа к знаниям") from None
