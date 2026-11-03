"""Public consultant proxy to the centrally configured managed RAG."""
import httpx
from fastapi import HTTPException


async def answer(settings, message, confirmed):
    if not confirmed:
        raise HTTPException(403, 'Подтвердите публичность вопроса для внешнего облака')
    try:
        async with httpx.AsyncClient(timeout=75, trust_env=False, follow_redirects=False) as client:
            response = await client.post(settings.source_url.rstrip('/') + '/api/v1/consultant-materials/yandex/chat',
                headers={'X-Publication-Key': settings.source_key},
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
        raise HTTPException(503, 'Yandex RAG временно недоступен') from None
