"""Use the isolated PostgreSQL/in-memory vector integration fixture."""
import pytest
from test_document_pipeline_integration import pipeline,pytestmark
from viio_documents.router import DocumentCreateRequest
from viio_documents.services.enhanced_retrieval import EnhancedRetrieval
from viio_documents.services.bulk_queue import BulkUploadTask

async def test_catalog_revocation_overrides_stale_vector_acl(pipeline):
    _,_,catalog,adapter,ingestion,_=pipeline
    item=await ingestion.index_text(DocumentCreateRequest(title='Canary guide',content='PRIVATE_REVOKED_CANARY support policy',is_public=True))
    data=ingestion.data
    first=await data.access_revision()
    retrieval=EnhancedRetrieval(adapter,data)
    assert await retrieval.search('PRIVATE_REVOKED_CANARY',{'role':'employee'},5)
    async with catalog.pool.connection() as conn:
        await conn.execute("UPDATE documents SET is_public=false,allowed_roles=ARRAY['admin'],allowed_departments=ARRAY[]::text[] WHERE id=%s",(item.document_id,))
    assert first!=await data.access_revision()
    # Qdrant intentionally retains the old public flag.
    assert not await retrieval.search('PRIVATE_REVOKED_CANARY',{'role':'employee'},5)
    assert await retrieval.search('PRIVATE_REVOKED_CANARY',{'role':'admin'},5)

async def test_old_version_download_obeys_current_family_acl(pipeline):
    client,_,catalog,_,ingestion,store=pipeline
    first=await ingestion.index_text(DocumentCreateRequest(title='Versioned guide',content='OLD_VERSION_CANARY',is_public=True))
    task=BulkUploadTask('version-revocation','new.txt',b'NEW_VERSION_CANARY','Versioned guide','admin',True,'text/plain',[],[],version_parent_id=first.document_id)
    current,_=await ingestion(task,store.save(task.data,suffix='.txt'))
    async with catalog.pool.connection() as conn:
        await conn.execute("UPDATE documents SET is_public=false,allowed_roles=ARRAY['admin'],allowed_departments=ARRAY[]::text[] WHERE id=%s",(current,))
    response=await client.get('/api/v1/documents/download/'+first.document_id,params={'user_role':'employee'})
    assert response.status_code==403 and 'OLD_VERSION_CANARY' not in response.text
    assert (await client.get('/api/v1/documents/download/'+first.document_id,params={'user_role':'admin'})).status_code==200
