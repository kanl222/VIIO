"""Apply document schema migrations under a deployment account, without workers."""
import asyncio
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from viio_documents.config import Settings
from viio_documents.infrastructure.persistence.document_catalog import DocumentCatalogService
from viio_documents.application.upload_jobs import UploadJobs

async def main():
    s=Settings()
    async with AsyncConnectionPool(make_conninfo(host=s.postgres_host,port=s.postgres_port,
        dbname=s.postgres_db,user=s.postgres_user,password=s.postgres_password),open=False,kwargs={'row_factory':dict_row}) as pool:
        catalog=DocumentCatalogService(pool)
        await catalog.setup()
        await UploadJobs(catalog).setup()
    print('Document schema migrated; no jobs or model calls started')

if __name__=='__main__':
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
