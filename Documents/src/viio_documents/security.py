"""Server-enforced service capabilities."""
from hmac import compare_digest
from fastapi import HTTPException

def authorize(request,settings):
    path=request.url.path
    if settings.service_role=='document-worker':expected=[settings.document_worker_key,settings.document_worker_key_previous]
    elif path.startswith('/api/v1/knowledge/') or path.startswith('/api/v1/documents/download/'):
        expected=[settings.documents_read_key,settings.documents_admin_key,settings.documents_read_key_previous,settings.documents_admin_key_previous]
    else:expected=[settings.documents_admin_key,settings.documents_admin_key_previous]
    if not settings.secure_runtime:expected.append(settings.internal_api_key)
    expected=[value for value in expected if value]
    if not expected:raise HTTPException(503,'Ключ доступа не настроен')
    token=request.headers.get('X-Internal-Token','').encode()
    if not any(compare_digest(token,value.encode()) for value in expected):
        raise HTTPException(401,'Недостаточно полномочий межсервисного ключа')
