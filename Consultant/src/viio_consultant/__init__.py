"""Standalone public API, intentionally independent of the APIAgent package."""
import logging
from contextlib import asynccontextmanager
from hmac import compare_digest
import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from viio_consultant.config import ConsultantSettings
from viio_consultant.models import create_model
from viio_consultant.agent import PublicConsultant
from viio_consultant.source import HttpPublicationSource

logger = logging.getLogger(__name__)


class Question(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=4000)


def create_app(settings=None, *, agent=None):
    settings = settings or ConsultantSettings()

    @asynccontextmanager
    async def lifespan(app):
        if settings.enabled and app.state.agent is None:
            async with httpx.AsyncClient(base_url=settings.source_url.rstrip('/'), timeout=10, trust_env=False,
                follow_redirects=False, headers={'X-Publication-Key': settings.source_key}) as client:
                app.state.agent = PublicConsultant(HttpPublicationSource(client), create_model(settings))
                yield
                app.state.agent = None
        else:
            yield

    app = FastAPI(title='VIIO Public Consultant', version='0.1.0', lifespan=lifespan)
    app.state.agent = agent

    @app.get('/health')
    async def health():
        return {'status': 'ok', 'enabled': settings.enabled}

    @app.post('/api/v1/consultant/chat')
    async def chat(request: Question, x_consultant_key: str | None = Header(default=None, alias='X-Consultant-Key')):
        if not settings.enabled or app.state.agent is None:
            raise HTTPException(503, 'Внешний консультант не настроен')
        if not x_consultant_key or not compare_digest(x_consultant_key.encode(), settings.api_key.encode()):
            raise HTTPException(401, 'Необходим ключ внешнего API')
        if not request.message.strip():
            raise HTTPException(400, 'Укажите вопрос')
        try:
            return await app.state.agent.chat(request.message.strip())
        except TimeoutError:
            raise HTTPException(504, 'Консультант занят. Повторите запрос позже.') from None
        except Exception:
            logger.exception('Consultant request failed')
            raise HTTPException(503, 'Консультант временно недоступен') from None

    return app
