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
                    headers={'X-Internal-Token': settings.internal_api_key or ''},
                    json={'query': query, 'k': k, 'user_context': context or None})
                response.raise_for_status()
                text = response.json()['context']
                if not isinstance(text, str) or len(text) > 100000:
                    raise ValueError('Invalid document search response')
                return text
        except (httpx.HTTPError, ValueError, KeyError):
            return 'Ошибка при обращении к базе знаний. Повторите попытку позже.'
    return search_knowledge_base


async def forward_documents(request: Request, base_url, settings):
    """Stream authorized compatibility requests without buffering uploaded files."""
    require_internal(request, settings)
    client = httpx.AsyncClient(timeout=180, trust_env=False, follow_redirects=False)
    url = httpx.URL(base_url.rstrip('/') + request.url.path, query=request.url.query.encode())
    headers = {'X-Internal-Token': settings.internal_api_key, 'Accept-Encoding': 'identity'}
    if request.headers.get('content-type'):
        headers['Content-Type'] = request.headers['content-type']
    try:
        upstream = await client.send(client.build_request(request.method, url,
            content=request.stream(), headers=headers), stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(503, 'Сервис документов временно недоступен') from None

    async def stream():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
    safe_headers = {key: value for key, value in upstream.headers.items()
                    if key.lower() in {'content-type', 'content-disposition', 'cache-control'}}
    return StreamingResponse(stream(), status_code=upstream.status_code, headers=safe_headers)
