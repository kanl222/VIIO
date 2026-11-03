"""Compatibility forwarding to the independent RAG process; no local fallback."""
import httpx
from fastapi import HTTPException
from fastapi.responses import Response

from viio_documents.security import authorize, authorize_publication


async def forward_rag(request, settings):
    publication = request.url.path.startswith('/api/v1/consultant-materials/')
    (authorize_publication if publication else authorize)(request, settings)
    name = 'X-Publication-Key' if publication else 'X-Internal-Token'
    # An already-authorized admin call becomes a read-only RAG call. The RAG
    # process never receives the document-management key.
    token = request.headers.get(name, '') if publication else (settings.documents_read_key or request.headers.get(name, ''))
    headers = {name: token, 'Content-Type': 'application/json'}
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 128000:
            raise HTTPException(413, 'Поисковый запрос слишком велик')
        body.extend(chunk)
    try:
        async with httpx.AsyncClient(timeout=settings.rag_timeout_seconds, trust_env=False, follow_redirects=False) as client:
            upstream = await client.request(request.method,
                settings.rag_url.rstrip('/') + request.url.path,
                content=bytes(body), headers=headers)
        return Response(upstream.content, status_code=upstream.status_code,
                        media_type='application/json')
    except httpx.HTTPError:
        raise HTTPException(503, 'Сервис поиска временно недоступен') from None
