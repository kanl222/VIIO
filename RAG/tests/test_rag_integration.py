import httpx
from viio_documents.config import Settings
from viio_rag.app import create_app
from viio_documents.router import DocumentCreateRequest
from test_document_pipeline_integration import pipeline, pytestmark


async def test_rag_http_observes_revocation_and_publication_withdrawal(pipeline):
    _, _, catalog, adapter, ingestion, _ = pipeline
    record = await ingestion.index_text(DocumentCreateRequest(title='Guide', content='REVOCATION_CANARY document', is_public=True))
    await ingestion.data.publish(record.document_id, 'Approved Guide', 'PUBLIC_SNAPSHOT_CANARY', 'admin')
    settings = Settings(_env_file=None, service_role='rag', documents_read_key='read', consultant_source_key='p' * 40)
    app = create_app(settings, data=ingestion.data, vector_store=adapter)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://rag') as client:
        query = {'query': 'REVOCATION_CANARY', 'user_context': {'role': 'employee'}}
        assert 'REVOCATION_CANARY' in (await client.post('/api/v1/knowledge/search', json=query, headers={'X-Internal-Token': 'read'})).text
        result = await client.post('/api/v1/consultant-materials/search', json={'message': 'PUBLIC_SNAPSHOT_CANARY'}, headers={'X-Publication-Key': 'p' * 40})
        assert 'PUBLIC_SNAPSHOT_CANARY' in result.text and 'REVOCATION_CANARY' not in result.text
        public_id = result.json()['items'][0]['id']
        await ingestion.data.withdraw(record.document_id)
        async with catalog.pool.connection() as conn:
            await conn.execute("UPDATE documents SET is_public=false,allowed_roles=ARRAY['admin'],allowed_departments=ARRAY[]::text[] WHERE id=%s", (record.document_id,))
        result = await client.post('/api/v1/knowledge/search', json=query, headers={'X-Internal-Token': 'read'})
        assert 'REVOCATION_CANARY' not in result.text
        result = await client.post('/api/v1/consultant-materials/validate', json={'publication_ids': [public_id]}, headers={'X-Publication-Key': 'p' * 40})
        assert result.json()['publication_ids'] == []
