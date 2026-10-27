"""Separate HTTP/process boundaries for agents, document reads and writes.

The services share domain code in one distribution; no duplicate catalog or index.
"""
from fastapi import Depends, HTTPException, Request
import anyio.to_thread
from pydantic import BaseModel, ConfigDict, Field

from assistent.router import UserContext
from assistent.services.documents_client import require_internal, forward_documents
from assistent.services.enhanced_retrieval import EnhancedRetrieval
from assistent.tools.search_tool import format_documents


class KnowledgeQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=4000)
    k: int = Field(default=5, ge=1, le=8)
    user_context: UserContext | None = None


def configure_service(app, api_router=None):
    settings = app.state.settings
    role = settings.service_role
    remote = app.state.remote_documents
    if not remote and role == 'assistent':
        return  # Explicit injected local adapters used by isolated unit/integration tests.
    router = api_router if api_router is not None else app.router
    if remote:
        retained = {'/api/v1/chat', '/api/v1/consultant/chat'}
    else:
        retained = {route.path for route in router.routes if route.path.startswith(
            ('/api/v1/documents', '/api/v1/consultant-materials'))}
    router.routes = [route for route in router.routes if route.path in retained
        or route.path in {'/docs', '/docs/oauth2-redirect', '/redoc', '/openapi.json'}]

    @app.get('/health')
    async def health():
        if role != 'assistent' and not app.state.catalog_service.available:
            raise HTTPException(503, 'Каталог документов недоступен')
        result = {'status': 'healthy', 'service': role}
        if role == 'assistent':
            result.update(provider=settings.llm_provider,
                model=settings.lmstudio_model if settings.llm_provider == 'lmstudio' else settings.ollama_model)
        if role == 'documents' and getattr(app.state, 'document_service', None) is not None:
            try:
                result['documents_count'] = await anyio.to_thread.run_sync(app.state.document_service.count_documents)
            except Exception:
                raise HTTPException(503, 'Поисковый индекс недоступен') from None
        return result

    if remote:
        @app.api_route('/api/v1/documents', methods=['GET', 'POST'], include_in_schema=False)
        @app.api_route('/api/v1/documents/{path:path}', methods=['GET', 'POST', 'DELETE'], include_in_schema=False)
        async def compatibility_documents(request: Request, path: str = ''):
            return await forward_documents(request, settings.documents_url, settings)
        return

    app.title = 'VIIO Documents' if role == 'documents' else 'VIIO Document Worker'

    def authorize(request: Request):
        require_internal(request, settings)

    if role == 'document-worker':
        @app.middleware('http')
        async def limit_processing(request, call_next):
            path = request.url.path
            heavy = request.method == 'POST' and path in {'/api/v1/documents', '/api/v1/documents/extract-pdf'}
            publication = request.method in {'POST','DELETE'} and path.startswith('/api/v1/documents/publication/')
            if heavy or publication:
                try:
                    require_internal(request, settings)
                except HTTPException as error:
                    from fastapi.responses import JSONResponse
                    return JSONResponse({'detail':error.detail},status_code=error.status_code)
                async with app.state.processing_slots:
                    return await call_next(request)
            return await call_next(request)

    if role == 'documents':
        @app.post('/api/v1/knowledge/search', dependencies=[Depends(authorize)])
        async def knowledge_search(question: KnowledgeQuestion):
            context = question.user_context.model_dump() if question.user_context else None
            docs = await EnhancedRetrieval(app.state.vector_store, app.state.knowledge_data).search(
                question.query, context, question.k)
            return {'context': format_documents(docs)}

        @app.middleware('http')
        async def delegate_writes(request, call_next):
            # Heavy synchronous-style APIs preserve their response contract, but
            # all processing runs in the worker process. Bulk uploads use the
            # durable queue directly and immediately return task identifiers.
            path = request.url.path
            heavy = request.method == 'POST' and path in {
                '/api/v1/documents', '/api/v1/documents/extract-pdf'}
            publication = request.method in {'POST', 'DELETE'} and path.startswith('/api/v1/documents/publication/')
            if heavy or publication:
                try:
                    return await forward_documents(request, settings.document_worker_url, settings)
                except HTTPException as error:
                    from fastapi.responses import JSONResponse
                    return JSONResponse({'detail': error.detail}, status_code=error.status_code)
            return await call_next(request)
