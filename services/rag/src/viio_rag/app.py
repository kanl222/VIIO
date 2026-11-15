"""Independent read-only retrieval process; no originals, queue or migrations."""
from contextlib import asynccontextmanager
import logging

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from qdrant_client import QdrantClient

from viio_rag.config import Settings
from viio_rag.embeddings import create_embeddings
from viio_rag.schemas import PublicQuestion, PublishedIdsRequest, KnowledgeQuestion, ManagedRagQuestion
from viio_rag.security import authorize, authorize_publication
from viio_rag.catalog import ReadCatalog
from viio_rag.cache import get_cache
from viio_rag.reranker import get_reranker
from viio_rag.retrieval import EnhancedRetrieval, fragment_snapshot
from viio_rag.diagnostics import capture, record
from viio_rag.knowledge_data import KnowledgeData
from viio_rag.publication_index import PublicationIndex
from viio_rag.publication_search import search_publications
from viio_rag.search_tool import format_documents
from viio_rag.schemas import DocumentListRequest
from viio_rag.document_directory import list_documents, search_selected_documents
from viio_rag.vector_store import QdrantVectorStoreAdapter


def create_app(settings=None, *, data=None, vector_store=None):
    s = settings or Settings(service_role='rag')
    if s.service_role != 'rag':
        raise ValueError('RAG must use SERVICE_ROLE=rag')
    s.validate_runtime()
    cache = get_cache(s)
    reranker = get_reranker(s)
    client = None
    catalog = None
    if data is None:
        client = QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key)
        embeddings = create_embeddings(s)
        vector_store = QdrantVectorStoreAdapter(client, embeddings, s.qdrant_collection_name)
        catalog = ReadCatalog()
        data = KnowledgeData(catalog, PublicationIndex(QdrantVectorStoreAdapter(
            client, embeddings, s.qdrant_public_collection_name)))

    @asynccontextmanager
    async def lifespan(app):
        if catalog is None:
            yield
            return
        try:
            async with AsyncConnectionPool(make_conninfo(host=s.postgres_host, port=s.postgres_port,
                dbname=s.postgres_db, user=s.postgres_user, password=s.postgres_password),
                open=False, min_size=1, max_size=8,
                kwargs={'row_factory': dict_row, 'options': '-c default_transaction_read_only=on'}) as pool:
                await pool.wait(timeout=20)
                catalog.set_pool(pool)
                try:
                    yield
                finally:
                    catalog.set_pool(None)
        finally:
            client.close()

    app = FastAPI(title='VIIO RAG', lifespan=lifespan,
                  docs_url=None if s.secure_runtime else '/docs', redoc_url=None if s.secure_runtime else '/redoc')
    app.state.knowledge_data = data
    app.state.vector_store = vector_store
    app.state.catalog_service = catalog

    @app.exception_handler(Exception)
    async def unavailable(request, error):
        logging.getLogger(__name__).warning('RAG request failed: %s', type(error).__name__)
        return JSONResponse({'detail': 'Сервис поиска временно недоступен'}, status_code=503)

    def internal(request: Request):
        authorize(request, s)

    def public(request: Request):
        authorize_publication(request, s)

    @app.get('/health')
    async def health():
        if catalog is not None:
            if not catalog.available:
                raise HTTPException(503, 'Каталог документов недоступен')
            async with catalog.pool.connection() as conn:
                await conn.execute('SELECT 1 FROM documents LIMIT 1')
        return {
            'status': 'healthy',
            'service': 'rag',
            'redis': await cache.ping(),
            'reranker': reranker.model_name if reranker.enabled else None,
        }

    @app.post('/api/v1/knowledge/yandex/chat', dependencies=[Depends(internal)])
    async def yandex_assistant(question: ManagedRagQuestion):
        from viio_rag.managed import answer
        return await answer(s, data, question, 'assistant')

    @app.post('/api/v1/consultant-materials/yandex/chat', dependencies=[Depends(public)])
    async def yandex_consultant(question: ManagedRagQuestion):
        from viio_rag.managed import answer
        return await answer(s, data, question, 'consultant')

    @app.post('/api/v1/knowledge/search', dependencies=[Depends(internal)])
    async def search(question: KnowledgeQuestion, req: Request):
        context = question.user_context.model_dump() if question.user_context else None
        diagnostic = {} if question.diagnostics else None
        parent_trace_id = req.headers.get('x-trace-id')
        if question.document_ids is not None:
            with capture('search', question.query, parent_trace_id=parent_trace_id) as trace:
                record('search_request', query=question.query, scope='internal', lookup_mode='document_ids')
                try:
                    docs = await search_selected_documents(data, question.query, question.document_ids, context)
                except Exception as error:
                    record('retrieval', query=question.query, scope='internal', lookup_mode='document_ids',
                           status='error', error=type(error).__name__)
                    raise
                text = format_documents(docs)
                if diagnostic is not None or trace is not None:
                    details = dict(lookup_mode='document_ids', requested_documents=len(question.document_ids),
                                   reranker_status='skipped', cache_status='skipped',
                                   selected=[dict(fragment_snapshot(doc), selection_reason='document_id') for doc in docs])
                    if trace is not None:
                        details['search_trace_id'] = trace['id']
                    if diagnostic is not None:
                        diagnostic.update(details)
                    record('retrieval', query=question.query, scope='internal', status='ok', context=text, **details)
        else:
            docs = await EnhancedRetrieval(vector_store, data, reranker=reranker, cache=cache).search(
                question.query, context, question.k, diagnostics=diagnostic, parent_trace_id=parent_trace_id
            )
            text = format_documents(docs)
        return {'context': text, **({'diagnostics': diagnostic} if diagnostic is not None else {})}

    @app.post('/api/v1/knowledge/documents', dependencies=[Depends(internal)])
    async def documents(request: DocumentListRequest):
        return await list_documents(data, request)

    @app.get('/api/v1/knowledge/access-revision', dependencies=[Depends(internal)])
    async def revision():
        return {'revision': await data.access_revision()}

    @app.post('/api/v1/consultant-materials/search', dependencies=[Depends(public)])
    async def publications(question: PublicQuestion):
        return await search_publications(data, question.message, question.diagnostics)

    @app.post('/api/v1/consultant-materials/validate', dependencies=[Depends(public)])
    async def validate_publications(question: PublishedIdsRequest):
        return {'publication_ids': sorted(await data.public_ids([str(identifier) for identifier in question.publication_ids]))}

    return app
