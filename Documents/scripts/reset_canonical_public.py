"""Explicit one-off reset of the VIIO KB to user-approved canonical text files."""
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import uuid

from dotenv import dotenv_values
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from qdrant_client import QdrantClient, models
from viio_documents.config import Settings
from viio_rag.embeddings import create_embeddings
from viio_documents.services.document_catalog import DocumentCatalogService
from viio_documents.services.document_ingestion import DocumentIngestion
from viio_documents.services.document_service import DocumentService
from viio_documents.services.file_store import LocalFileStore
from viio_rag.knowledge_data import KnowledgeData
from viio_rag.publication_index import PublicationIndex
from viio_documents.services.bulk_queue import BulkUploadTask
from viio_rag.access import can_read_document
from viio_rag.vector_store import QdrantVectorStoreAdapter

ROOT = Path(__file__).resolve().parents[2]


async def main():
    logging.disable(logging.CRITICAL)
    values = {**dotenv_values(ROOT / '.env'), **os.environ}
    approved = json.loads((ROOT / 'docs/yandex-public-canonical-approval.json').read_text(encoding='utf-8'))
    sources = []
    for row in approved['files']:
        path = Path(row['path'])
        assert path.parent.resolve() == (ROOT / 'data/gosapteka56/2026-10-27/canonical').resolve()
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row['sha256'], path.name
        text = raw.decode('utf-8-sig')
        assert text.strip() and '\x00' not in text
        title = next((line.lstrip('# ').strip() for line in text.splitlines() if line.strip()), path.stem)[:255]
        sources.append((path, raw, text, title))
    assert len(sources) == 41
    assert (ROOT / 'tmp/kb-backup-2026-11-02/agent_db.dump').stat().st_size > 0
    s = Settings(_env_file=None, service_role='document-worker',
        lmstudio_base_url='http://127.0.0.1:1234/v1',
        lmstudio_api_key=values.get('LMSTUDIO_API_KEY', 'lm-studio'),
        lmstudio_embedding_model=values.get('LMSTUDIO_EMBEDDING_MODEL', 'text-embedding-nomic-embed-text-v1.5'),
        qdrant_url='http://127.0.0.1:6333', qdrant_api_key=values.get('QDRANT_API_KEY'),
        qdrant_collection_name=values.get('QDRANT_COLLECTION_NAME', 'knowledge_base'),
        qdrant_public_collection_name=values.get('QDRANT_PUBLIC_COLLECTION_NAME', 'knowledge_public'))
    embeddings = create_embeddings(s)
    assert len(embeddings.embed_query('public indexing check')) > 0
    client = QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key, timeout=120)
    vectors = QdrantVectorStoreAdapter(client, embeddings, s.qdrant_collection_name)
    public = PublicationIndex(QdrantVectorStoreAdapter(client, embeddings, s.qdrant_public_collection_name))
    conn = make_conninfo(host='127.0.0.1', port=values.get('POSTGRES_PORT', '5432'), dbname='agent_db',
        user=values.get('POSTGRES_USER', 'postgres'), password=values['POSTGRES_PASSWORD'])
    async with AsyncConnectionPool(conn, open=False, kwargs={'row_factory': dict_row}) as pool:
        await pool.wait()
        catalog = DocumentCatalogService(pool)
        data = KnowledgeData(catalog, public)
        store = LocalFileStore(ROOT / 'data/public-canonical-storage')
        ingestion = DocumentIngestion(DocumentService(vectors), catalog, store, s, data)
        async with pool.connection() as connection:
            await connection.execute('DELETE FROM document_upload_jobs')
            await connection.execute('DELETE FROM documents')
        for collection in (s.qdrant_collection_name, s.qdrant_public_collection_name):
            if client.collection_exists(collection):
                client.delete(collection, points_selector=models.FilterSelector(filter=models.Filter()), wait=True)
        print(json.dumps({'stage': 'cleared', 'source_files': len(sources)}), flush=True)
        results = []
        async def index(raw, text, title, filename):
            task = BulkUploadTask(uuid.uuid4().hex, filename, raw, title,
                'canonical-public-2026-11-02', True, 'text/markdown', [], [],
                reviewed_content=text, metadata={'input_format': 'manual-text', 'canonical_source': filename})
            key = store.save(raw, suffix='.txt')
            identifier, chunks = await ingestion(task, key)
            record = await catalog.get(identifier)
            assert can_read_document(record, {'role': 'guest'})
            return identifier, chunks
        for path, raw, text, title in sources:
            identifier, chunks = await index(raw, text, title, path.name)
            publications = []
            parts = [text[start:start + 44000] for start in range(0, len(text), 44000)]
            for number, part in enumerate(parts, 1):
                part_title = title if len(parts) == 1 else title[:220] + f' — часть {number}/{len(parts)}'
                part_id = identifier
                if len(parts) > 1:
                    part_id, _ = await index(part.encode(), part, part_title, f'{path.stem}-part-{number}.txt')
                await data.publish(part_id, part_title, part, 'canonical-public-2026-11-02')
                publication = await data.publication(part_id)
                publications.append({'id': publication['id'], 'document_id': part_id})
            results.append({'file': path.name, 'document_id': identifier, 'chunks': chunks,
                'sha256': hashlib.sha256(raw).hexdigest(), 'publications': publications, 'guest_allowed': True})
            print(json.dumps({'stage': 'imported', 'file': path.name, 'publications': len(publications)}), flush=True)
        report = {'source_files': len(results), 'publications': sum(len(r['publications']) for r in results),
            'results': results}
        (ROOT / 'docs/public-canonical-import-2026-11-02.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'stage': 'complete', 'source_files': report['source_files'],
            'publications': report['publications']}), flush=True)
    client.close()


if __name__ == '__main__':
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
