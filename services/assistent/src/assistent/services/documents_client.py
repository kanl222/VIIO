"""Async knowledge client; assistant never indexes documents locally."""
from hmac import compare_digest

import httpx
import time
import json
from assistent.services.diagnostics import enabled, record, trace_headers
from assistent.services.answer_sources import readable_reference, sources_from_reference
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool


def require_internal(request, settings):
    token = request.headers.get('X-Internal-Token', '')
    if not settings.internal_api_key:
        raise HTTPException(503, 'Межсервисный ключ не настроен')
    if not compare_digest(token.encode(), settings.internal_api_key.encode()):
        raise HTTPException(401, 'Необходим межсервисный ключ')


def create_remote_search_tool(settings, k=5, *, tool_name='search_knowledge_base'):
    @tool(tool_name, response_format='content_and_artifact')
    async def search_knowledge_base(query: str, config: RunnableConfig, document_ids: list[str] | None = None) -> tuple[str, dict]:
        """Единый гибридный поиск по базе знаний (GraphRAG):
        - Онтология аптечного склада: расшифровка аббревиатур (ИСЛО, МДЛП, ЕГИСЗ), бизнес-процессы, шаги алгоритмов, температурные режимы хранения, информационные системы и нормативные акты.
        - Содержимое регламентирующих документов, инструкций и руководств.

        Используйте этот инструмент по умолчанию для любых вопросов о терминах, регламентах, алгоритмах и инструкциях.
        document_ids: Необязательный массив точных doc_id для фильтрации, например ["doc-123"].
        """
        context = (config or {}).get('configurable', {}).get('user_context') or {}
        # Личные заметки и данные диалогов никогда не попадают в поиск документов.
        context = {key: context[key] for key in ('user_id', 'role', 'departments') if key in context}
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.post(settings.documents_url.rstrip('/') + '/api/v1/knowledge/search',
                    headers={
                        'X-Internal-Token': settings.documents_read_key or (settings.internal_api_key if not settings.secure_runtime else '') or '',
                        **trace_headers(),
                    },
                    json={'query': query, 'k': k, 'user_context': context or None,
                          **({'document_ids': document_ids} if document_ids is not None else {}),
                          **({'diagnostics': True} if enabled() else {})})
                response.raise_for_status()
                payload = response.json()
                text = payload['context']
                if not isinstance(text, str) or len(text) > 100000:
                    raise ValueError('Invalid document search response')
                sources = sources_from_reference(text)
                max_chars = getattr(settings, 'rag_context_max_chars', None)
                if max_chars and tool_name == 'search_knowledge_base':
                    text = readable_reference(text, max_chars=max_chars, include_ids=True)
                record('retrieval', query=query, duration_ms=round((time.monotonic()-start)*1000),
                       context=text, **payload.get('diagnostics', {}))
                return text, {'sources': sources}
        except (httpx.HTTPError, ValueError, KeyError):
            record('retrieval', query=query, status='error', duration_ms=round((time.monotonic()-start)*1000))
            return 'Ошибка при обращении к базе знаний. Повторите попытку позже.', {}
    return search_knowledge_base


def create_document_list_tool(settings):
    @tool('list_documents', response_format='content_and_artifact')
    async def list_documents(config: RunnableConfig, query: str = '', tags: list[str] | None = None,
                             offset: int = 0) -> tuple[str, dict]:
        """Каталог доступных документов, без чтения содержимого.

        Для вопросов о доступных документах, инструкциях и руководствах, поиска названий и ссылок.
        query: Подстрока названия или описания, пустая строка для полного перечня.
        tags: Массив требуемых тегов, например ["инструкция", "ис ло"]. Необязательный аргумент.
        offset: Начало страницы. Продолжение по next_offset только по просьбе пользователя.
        Возвращает одну страницу, total, next_offset и untagged_count. Один вызов за ход.
        """
        context = (config or {}).get('configurable', {}).get('user_context') or {}
        context = {key: context[key] for key in ('user_id', 'role', 'departments') if key in context}
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.post(settings.documents_url.rstrip('/') + '/api/v1/knowledge/documents',
                    headers={
                        'X-Internal-Token': settings.documents_read_key or
                                             (settings.internal_api_key if not settings.secure_runtime else '') or '',
                        **trace_headers(),
                    },
                    json={'query': query, 'tags': tags or [], 'offset': offset, 'limit': 20,
                          'user_context': context or None})
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError):
            record('document_catalog', status='error')
            return 'Каталог недоступен. Не удалось проверить список документов. Повторите запрос позже.', {'sources': []}
        sources = [{'title': item['title'], 'document_id': item['document_id']}
                   for item in result['items']]
        record('document_catalog', total=result['total'], count=len(result['items']), offset=offset)
        return json.dumps(result, ensure_ascii=False), {'sources': sources}
    return list_documents


async def access_revision(settings):
    try:
        async with httpx.AsyncClient(timeout=10,trust_env=False) as client:
            response=await client.get(settings.documents_url.rstrip('/')+'/api/v1/knowledge/access-revision',
                headers={'X-Internal-Token':settings.documents_read_key or ''})
            response.raise_for_status()
            revision=response.json()['revision']
            if not isinstance(revision,str) or len(revision)!=32:raise ValueError('Invalid revision')
            return revision
    except (httpx.HTTPError,KeyError,ValueError):
        raise HTTPException(503,'Не удалось проверить актуальность доступа к знаниям') from None
