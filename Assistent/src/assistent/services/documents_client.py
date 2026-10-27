"""Async knowledge client; assistant never indexes documents locally."""
from hmac import compare_digest

import httpx
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


def create_remote_search_tool(settings, k=5):
    @tool('search_knowledge_base')
    async def search_knowledge_base(query: str, config: RunnableConfig) -> str:
        """Поиск разрешённых инструкций, документов и кода организации в общей базе знаний."""
        context = (config or {}).get('configurable', {}).get('user_context') or {}
        # Personal notes and conversation data never enter document search.
        context = {key: context[key] for key in ('user_id', 'role', 'departments') if key in context}
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                response = await client.post(settings.documents_url.rstrip('/') + '/api/v1/knowledge/search',
                    headers={'X-Internal-Token': settings.documents_read_key or (settings.internal_api_key if not settings.secure_runtime else '') or ''},
                    json={'query': query, 'k': k, 'user_context': context or None})
                response.raise_for_status()
                text = response.json()['context']
                if not isinstance(text, str) or len(text) > 100000:
                    raise ValueError('Invalid document search response')
                return text
        except (httpx.HTTPError, ValueError, KeyError):
            return 'Ошибка при обращении к базе знаний. Повторите попытку позже.'
    return search_knowledge_base


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
