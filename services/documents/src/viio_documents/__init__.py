"""Independent document API and worker; no imports from either agent."""
import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from qdrant_client import QdrantClient

from viio_documents.config import Settings
from viio_rag.embeddings import create_embeddings
from viio_documents.router import create_api_router
from viio_documents.services.document_catalog import DocumentCatalogService
from viio_documents.services.document_ingestion import DocumentIngestion
from viio_documents.services.document_service import DocumentService
from viio_documents.services.file_store import LocalFileStore
from viio_documents.services.project_memory import COLLECTION as PROJECT_MEMORY_COLLECTION, register_project_memory
from viio_rag.knowledge_data import KnowledgeData
from viio_documents.services.persistent_bulk_queue import PersistentBulkQueue
from viio_rag.publication_index import PublicationIndex
from viio_documents.service_profiles import configure_service
from viio_rag.vector_store import QdrantVectorStoreAdapter

logger=logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    s=app.state.settings
    async with AsyncConnectionPool(make_conninfo(host=s.postgres_host,port=s.postgres_port,
        dbname=s.postgres_db,user=s.postgres_user,password=s.postgres_password),
        open=False,kwargs={'row_factory':dict_row},min_size=1,max_size=s.bulk_max_workers+4) as pool:
        app.state.catalog_service.set_pool(pool)
        if s.database_migrations_enabled:
            await app.state.catalog_service.setup()
            await app.state.bulk_queue.setup()
        worker=s.service_role=='document-worker'
        if worker:
            await app.state.ingestion_service.recover_versions()
            app.state.bulk_queue.start()
        async def reconcile():
            while True:
                try:
                    async with app.state.processing_slots:
                        await app.state.knowledge_data.sync_public_index()
                except Exception:
                    logger.exception('Public index reconciliation failed')
                await asyncio.sleep(30)
        task=asyncio.create_task(reconcile()) if worker else None
        try:yield
        finally:
            if task:
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
            if worker:await app.state.bulk_queue.stop()
            app.state.catalog_service.set_pool(None)
            if app.state.qdrant_client:app.state.qdrant_client.close()


def create_app(settings=None, agent_service=None, document_service=None, checkpointer=None):
    s=settings or Settings()
    s.validate_runtime()
    client=QdrantClient(url=s.qdrant_url,api_key=s.qdrant_api_key) if document_service is None else None
    vectors=None
    if client:
        embeddings=create_embeddings(s)
        vectors=QdrantVectorStoreAdapter(client,embeddings,s.qdrant_collection_name)
        document_service=DocumentService(vectors,chunk_size=s.rag_chunk_size,
            chunk_overlap=s.rag_chunk_overlap,chunk_unit=s.rag_chunk_unit)
    catalog=DocumentCatalogService(None)
    data=KnowledgeData(catalog,PublicationIndex(QdrantVectorStoreAdapter(client,embeddings,
        s.qdrant_public_collection_name)) if client else None)
    store=LocalFileStore(s.documents_storage_path)
    slots=asyncio.Semaphore(s.bulk_max_workers)
    ingestion=DocumentIngestion(document_service,catalog,store,s,knowledge_data=data)
    queue=PersistentBulkQueue(ingestion,catalog,store,max_workers=s.bulk_max_workers,processing_slots=slots)
    app=FastAPI(title='VIIO Documents',lifespan=lifespan,docs_url=None if s.secure_runtime else '/docs',redoc_url=None if s.secure_runtime else '/redoc')
    @app.middleware('http')
    async def service_boundary(request,call_next):
        if request.url.path.startswith(('/api/v1/documents','/api/v1/knowledge/','/api/v1/project-memory/')):
            from viio_documents.security import authorize
            from fastapi import HTTPException
            from fastapi.responses import JSONResponse
            try:authorize(request,s)
            except HTTPException as error:return JSONResponse({'detail':error.detail},status_code=error.status_code)
        return await call_next(request)
    for key,value in dict(settings=s,catalog_service=catalog,knowledge_data=data,file_store=store,
        ingestion_service=ingestion,document_service=document_service,bulk_queue=queue,
        vector_store=vectors,processing_slots=slots,remote_documents=False,qdrant_client=client).items():
        setattr(app.state,key,value)
    router=create_api_router(None,document_service,s,catalog,store,queue,ingestion,data)
    configure_service(app,router)
    app.include_router(router)
    if client and s.service_role != 'documents':
        if PROJECT_MEMORY_COLLECTION in {s.qdrant_collection_name, s.qdrant_public_collection_name}:
            raise ValueError('Память проектов требует отдельной коллекции Qdrant')
        register_project_memory(app,QdrantVectorStoreAdapter(client,embeddings,PROJECT_MEMORY_COLLECTION))
    return app
