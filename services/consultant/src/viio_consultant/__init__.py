"""Standalone public API, intentionally independent of the assistent package."""
import json
import logging
from viio_consultant.diagnostics import capture, record
from contextlib import asynccontextmanager
from hmac import compare_digest
import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from typing import Literal
from viio_consultant.config import ConsultantSettings
from viio_consultant.models import create_model
from viio_consultant.agent import PublicConsultant
from viio_consultant.source import HttpPublicationSource

logger = logging.getLogger(__name__)


class Question(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=4000)
    confirmed_public_for_external_cloud: StrictBool = False
    rag_backend: Literal['local', 'yandex'] | None = None


def create_app(settings=None, *, agent=None):
    settings = settings or ConsultantSettings()

    @asynccontextmanager
    async def lifespan(app):
        if settings.enabled and app.state.agent is None and settings.rag_backend == 'local':
            async with httpx.AsyncClient(base_url=settings.source_url.rstrip('/'), timeout=10, trust_env=False,
                follow_redirects=False, headers={'X-Publication-Key': settings.source_key}) as client:
                app.state.agent = PublicConsultant(HttpPublicationSource(client,
                    cloud_public_only=settings.provider == 'yandex'), create_model(settings),
                    search_first=settings.search_first or settings.provider == 'yandex')
                yield
                app.state.agent = None
        else:
            yield

    app = FastAPI(title='VIIO Public Consultant', version='0.1.0', lifespan=lifespan)
    app.state.agent = agent
    from viio_consultant.yandex import cloud_router
    app.include_router(cloud_router(settings))

    @app.get('/health')
    async def health():
        return {'status': 'ok', 'enabled': settings.enabled}

    @app.post('/api/v1/consultant/chat')
    async def chat(request: Question, x_consultant_key: str | None = Header(default=None, alias='X-Consultant-Key')):
        if not settings.enabled:
            raise HTTPException(503, 'Внешний консультант не настроен')
        if not x_consultant_key or not compare_digest(x_consultant_key.encode(), settings.api_key.encode()):
            raise HTTPException(401, 'Необходим ключ внешнего API')
        if not request.message.strip():
            raise HTTPException(400, 'Укажите вопрос')
        if (request.rag_backend or settings.rag_backend) == 'yandex':
            from viio_consultant.managed_rag import answer
            return await answer(settings, request.message.strip(), request.confirmed_public_for_external_cloud)
        if app.state.agent is None:
            raise HTTPException(503, 'Внешний консультант не настроен')
        if settings.provider == 'yandex' and not request.confirmed_public_for_external_cloud:
            raise HTTPException(403, 'Подтвердите публичность вопроса для Yandex Cloud')
        with capture('consultant', request.message):
            record('route', mode='published_documents', tools=['search_published_documents'])
            try:
                result = await app.state.agent.chat(request.message.strip())
                record('answer', reply=result['reply'], sources=result['sources'])
                return result
            except TimeoutError:
                raise HTTPException(504, 'Консультант занят. Повторите запрос позже.') from None
            except Exception:
                logger.exception('Consultant request failed')
                raise HTTPException(503, 'Консультант временно недоступен') from None

    @app.post('/api/v1/consultant/chat/stream')
    async def chat_stream(request: Question, x_consultant_key: str | None = Header(default=None, alias='X-Consultant-Key')):
        if not settings.enabled:
            raise HTTPException(503, 'Внешний консультант не настроен')
        if not x_consultant_key or not compare_digest(x_consultant_key.encode(), settings.api_key.encode()):
            raise HTTPException(401, 'Необходим ключ внешнего API')
        if not request.message.strip():
            raise HTTPException(400, 'Укажите вопрос')
        if app.state.agent is None:
            raise HTTPException(503, 'Внешний консультант не настроен')

        async def sse_events():
            with capture('consultant', request.message):
                record('route', mode='published_documents', tools=['search_published_documents'])
                try:
                    final_res = None
                    async for event in app.state.agent.astream(request.message.strip()):
                        if event.get('type') == 'done':
                            final_res = event
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if final_res:
                        record('answer', reply=final_res.get('reply'), sources=final_res.get('sources', []))
                except TimeoutError:
                    err_ev = {'type': 'error', 'detail': 'Консультант занят. Повторите запрос позже.'}
                    yield f"data: {json.dumps(err_ev, ensure_ascii=False)}\n\n"
                except Exception:
                    logger.exception('Consultant streaming request failed')
                    err_ev = {'type': 'error', 'detail': 'Консультант временно недоступен'}
                    yield f"data: {json.dumps(err_ev, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            sse_events(),
            media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'},
        )

    return app
