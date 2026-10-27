"""Bounded forwarding from the document API to its processing worker."""
from hmac import compare_digest
import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

def require_internal(request,settings):
    from viio_documents.security import authorize
    authorize(request,settings)

async def forward_documents(request,base_url,settings):
    require_internal(request,settings)
    client=httpx.AsyncClient(timeout=180,trust_env=False,follow_redirects=False)
    token=settings.document_worker_key or (settings.internal_api_key if not settings.secure_runtime else '')
    headers={'X-Internal-Token':token,'Accept-Encoding':'identity'}
    if request.headers.get('content-type'):headers['Content-Type']=request.headers['content-type']
    url=httpx.URL(base_url.rstrip('/')+request.url.path,query=request.url.query.encode())
    try:upstream=await client.send(client.build_request(request.method,url,content=request.stream(),headers=headers),stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(503,'Обработчик документов временно недоступен') from None
    async def stream():
        try:
            async for chunk in upstream.aiter_bytes():yield chunk
        finally:
            await upstream.aclose();await client.aclose()
    return StreamingResponse(stream(),status_code=upstream.status_code,headers={k:v for k,v in upstream.headers.items()
        if k.lower() in {'content-type','content-disposition','cache-control'}})
