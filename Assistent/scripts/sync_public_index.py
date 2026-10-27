"""Reconcile published snapshots; never imports or republishes source documents."""
import asyncio
import json

from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from qdrant_client import QdrantClient

from assistent.config import Settings
from assistent.models import create_embeddings
from assistent.services.document_catalog import DocumentCatalogService
from assistent.services.knowledge_data import KnowledgeData
from assistent.services.publication_index import PublicationIndex
from assistent.utils.vector_store import QdrantVectorStoreAdapter


async def main():
    settings = Settings()
    if settings.service_role != 'document-worker':
        raise RuntimeError('Run index maintenance in viio_document_worker')
    client = QdrantClient(url=settings.qdrant_url)
    try:
        async with AsyncConnectionPool(make_conninfo(host=settings.postgres_host,
            port=settings.postgres_port, dbname=settings.postgres_db, user=settings.postgres_user,
            password=settings.postgres_password), kwargs={'row_factory': dict_row}, open=False) as pool:
            data = KnowledgeData(DocumentCatalogService(pool), PublicationIndex(QdrantVectorStoreAdapter(
                client, create_embeddings(settings), settings.qdrant_public_collection_name)))
            await data.sync_public_index()
            inventory = await data.public_index.inventory()
            valid = await data.public_ids(list(inventory))
            print(json.dumps(dict(collection=settings.qdrant_public_collection_name,
                publications=len(inventory), current_publications=len(valid),
                chunks=sum(map(len, inventory.values())))), flush=True)
            assert set(inventory) == valid
    finally:
        client.close()


if __name__ == '__main__':
    asyncio.run(main())
