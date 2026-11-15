"""Read-only capabilities for RAG; management keys are never accepted."""
from hmac import compare_digest
from fastapi import HTTPException


def authorize(request, settings):
    accepted = [settings.documents_read_key, settings.documents_read_key_previous]
    if not settings.secure_runtime:
        accepted.append(settings.internal_api_key)
    accepted = [key for key in accepted if key]
    if not accepted:
        raise HTTPException(503, 'Ключ доступа не настроен')
    token = request.headers.get('X-Internal-Token', '').encode()
    if not any(compare_digest(token, key.encode()) for key in accepted):
        raise HTTPException(401, 'Необходим ключ чтения')


def authorize_publication(request, settings):
    expected = settings.consultant_source_key
    if not expected or len(expected) < 32 or expected in {settings.internal_api_key, settings.consultant_api_key}:
        raise HTTPException(503, 'Источник публикаций не настроен')
    if not compare_digest(request.headers.get('X-Publication-Key', '').encode(), expected.encode()):
        raise HTTPException(401, 'Необходим служебный ключ публикаций')
