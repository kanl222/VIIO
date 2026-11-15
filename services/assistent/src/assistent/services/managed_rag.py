"""Public cloud mode bypasses local history, personal memory, tools and databases."""
from fastapi import HTTPException
from viio_core.cloud import query_managed_rag, ManagedRagError


async def answer(settings, message, confirmed):
    url = settings.documents_url.rstrip('/') + '/api/v1/knowledge/yandex/chat'
    try:
        return await query_managed_rag(
            endpoint_url=url,
            auth_header_name='X-Internal-Token',
            auth_token=settings.documents_read_key or '',
            message=message,
            confirmed_public=confirmed,
        )
    except ManagedRagError as e:
        if e.status_code == 503:
            raise HTTPException(503, 'Yandex RAG временно недоступен; локальный режим можно выбрать в настройках') from None
        raise HTTPException(e.status_code, e.detail) from None
