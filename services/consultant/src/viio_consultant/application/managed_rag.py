"""Public consultant proxy to the centrally configured managed RAG."""
from fastapi import HTTPException
from viio_core.cloud import query_managed_rag, ManagedRagError


async def answer(settings, message, confirmed):
    url = settings.source_url.rstrip('/') + '/api/v1/consultant-materials/yandex/chat'
    try:
        return await query_managed_rag(
            endpoint_url=url,
            auth_header_name='X-Publication-Key',
            auth_token=settings.source_key,
            message=message,
            confirmed_public=confirmed,
        )
    except ManagedRagError as e:
        raise HTTPException(e.status_code, e.detail) from None
