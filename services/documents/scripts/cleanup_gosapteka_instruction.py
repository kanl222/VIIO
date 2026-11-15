"""Version the four known imported instruction records; dry-run unless --apply.

Run in document-worker. Original versions/files remain as rollback evidence.
Only whitespace cleanup of this explicitly identified PDF import is authorized.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import uuid

from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from viio_documents import create_app
from viio_documents.config import Settings
from viio_documents.services.bulk_queue import BulkUploadTask
from viio_documents.services.text_normalization import normalize_document_text

SOURCE = 'https://gosapteka56.ru/files/Farmkomitet/is_lo/docs_apt_uchet/01-instruct-23.05.22.pdf'
OWNER = 'gosapteka56-public-import-2026-10-27'
DOCUMENTS = [
    'doc-5b476c8a1113590ab68146fba398dfb7',
    'doc-49dc6c6359a55ea0868a581f3fa165d5',
    'doc-0833619fc5e25fe6ae838bb8fe2c0940',
    'doc-6f594088515a565696930eaa395f66f7',
]


async def main(apply=False):
    settings = Settings()
    if settings.service_role != 'document-worker':
        raise RuntimeError('Run only in document-worker')
    app = create_app(settings=settings)
    dsn = make_conninfo(host=settings.postgres_host, port=settings.postgres_port,
        dbname=settings.postgres_db, user=settings.postgres_user, password=settings.postgres_password)
    async with AsyncConnectionPool(dsn, open=False, kwargs={'row_factory': dict_row},
                                   min_size=1, max_size=5) as pool:
        catalog = app.state.catalog_service
        catalog.set_pool(pool)
        ingestion = app.state.ingestion_service
        data = ingestion.data
        for identifier in DOCUMENTS:
            parent = await catalog.get(identifier)
            if parent is None or parent.owner_id != OWNER or parent.document_kind != 'document':
                raise ValueError('Unexpected document ownership or kind')
            versions = await catalog.versions(parent.family_id or parent.id)
            current = next(row for row in versions if row.is_active)
            async with pool.connection() as conn:
                row = await (await conn.execute(
                    'SELECT content,filename FROM document_contents WHERE document_id=%s',
                    (current.id,))).fetchone()
            if not row or SOURCE not in row['content'][:1500]:
                raise ValueError('Unexpected source document')
            before = row['content']
            cleaned = normalize_document_text(before)
            async with pool.connection() as conn:
                publication = await (await conn.execute(
                    'SELECT id::text,title,content,publisher_id FROM document_publications WHERE document_id=%s',
                    (identifier,))).fetchone()
            # Предыдущий прерванный запуск мог уже активировать очищенную версию.
            report = dict(old_id=identifier, current_id=current.id, title=current.title,
                          before_characters=len(before), after_characters=len(cleaned), applied=False)
            if not apply:
                report['publication_pending'] = bool(publication and current.id != identifier)
                print(json.dumps(report, ensure_ascii=False), flush=True)
                continue
            if cleaned != before:
                original = ingestion.store.load(current.storage_key)
                if hashlib.sha256(original).hexdigest() != current.sha256:
                    raise ValueError('Original checksum mismatch')
                is_pdf = current.mime_type == 'application/pdf'
                payload = original if is_pdf else cleaned.encode('utf-8')
                filename = row['filename']
                task = BulkUploadTask(
                    task_id=uuid.uuid5(uuid.NAMESPACE_URL, 'whitespace-v1:' + current.id).hex,
                    filename=filename, data=payload, title=current.title,
                    owner_id=current.owner_id, is_public=current.is_public,
                    mime_type=current.mime_type, allowed_roles=current.allowed_roles,
                    allowed_departments=current.allowed_departments,
                    reviewed_content=cleaned, version_parent_id=current.id,
                    metadata={'source_url': SOURCE, 'text_normalization': 'prose-v1',
                              'extraction_engine': 'reviewed-whitespace-maintenance'},
                )
                key = ingestion.store.save(payload, suffix=Path(filename).suffix)
                new_id, chunks = await ingestion(task, key)
                report.update(current_id=new_id, chunks=chunks, applied=True)
                current = await catalog.get(new_id)
            if publication and current.id != identifier:
                # Повторно используем существующий новый снапшот после прерванной успешной публикации.
                existing = await data.publication(current.id)
                if not existing or existing['content'] != cleaned:
                    await data.publish(current.id, publication['title'], cleaned, publication['publisher_id'])
                await data.withdraw(identifier)
                report['publication_moved'] = True
            updated = await catalog.get(current.id)
            assert (updated.owner_id, updated.is_public, updated.allowed_roles, updated.allowed_departments) == (
                parent.owner_id, parent.is_public, parent.allowed_roles, parent.allowed_departments)
            assert hashlib.sha256(ingestion.store.load(parent.storage_key)).hexdigest() == parent.sha256
            print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    asyncio.run(main(parser.parse_args().apply))
