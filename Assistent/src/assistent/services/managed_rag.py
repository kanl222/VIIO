"""Public cloud mode bypasses local history, personal memory, tools and databases."""
import httpx
from fastapi import HTTPException


async def answer(settings, message, confirmed):
    if not confirmed:
        raise HTTPException(403, 'Подтвердите публичность вопроса для внешнего облака')
    try:
        async with httpx.AsyncClient(timeout=75, trust_env=False, follow_redirects=False) as client:
            response = await client.post(settings.documents_url.rstrip('/') + '/api/v1/knowledge/yandex/chat',
                headers={'X-Internal-Token': settings.documents_read_key or ''},
                json={'message': message, 'confirmed_public_for_external_cloud': True})
        if response.status_code in (400, 403, 422):
            raise HTTPException(response.status_code, 'Проверьте вопрос и подтверждение его публичности')
        response.raise_for_status()
        result = response.json()
        if not isinstance(result.get('reply'), str) or not isinstance(result.get('sources'), list):
            raise ValueError('Invalid managed RAG response')
        return result
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, 'Yandex RAG временно недоступен; локальный режим можно выбрать в настройках') from None
