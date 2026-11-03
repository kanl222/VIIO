"""Explicitly approved public text only; no retrieval, history or tools."""
import httpx
from fastapi import APIRouter, Header, HTTPException
from hmac import compare_digest
from pydantic import BaseModel, ConfigDict, Field, StrictBool


class PublicCloudQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=4000)
    confirmed_public_for_external_cloud: StrictBool


def cloud_router(settings):
    router = APIRouter()

    @router.post('/api/v1/consultant/yandex/chat')
    async def chat(request: PublicCloudQuestion,
                   key: str | None = Header(default=None, alias='X-Consultant-Key')):
        if not settings.yandex_enabled:
            raise HTTPException(503, 'Yandex Cloud не настроен')
        if not key or not compare_digest(key.encode(), settings.api_key.encode()):
            raise HTTPException(401, 'Необходим ключ внешнего API')
        if request.confirmed_public_for_external_cloud is not True:
            raise HTTPException(403, 'Подтвердите публичность текста для внешнего облака')
        if not request.message.strip():
            raise HTTPException(400, 'Укажите вопрос')
        model = settings.yandex_model
        if not model.startswith('gpt://'):
            model = f'gpt://{settings.yandex_folder_id}/{model}'
        try:
            async with httpx.AsyncClient(timeout=60, trust_env=False, follow_redirects=False) as client:
                response = await client.post('https://ai.api.cloud.yandex.net/v1/chat/completions',
                    headers={'Authorization': f'Api-Key {settings.yandex_api_key}',
                             'OpenAI-Project': settings.yandex_folder_id},
                    json={'model': model, 'messages': [{'role': 'user', 'content': request.message.strip()}],
                          'temperature': settings.temperature, 'max_tokens': settings.max_tokens})
                response.raise_for_status()
                reply = response.json()['choices'][0]['message']['content']
                if not isinstance(reply, str):
                    raise ValueError('Invalid response')
                return {'reply': reply, 'sources': []}
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            raise HTTPException(503, 'Yandex Cloud временно недоступен') from None

    return router
