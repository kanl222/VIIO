"""Local smoke check: begin, prepared, restart assistent, verify, cleanup.

Run inside the configured assistent container. Only fixture IDs recorded in the
state file are removed; ordinary documents are never selected for deletion.
"""
import asyncio
import json
from pathlib import Path
import sys
import uuid

import httpx
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from qdrant_client import QdrantClient, models
from assistent.config import Settings
from assistent.services.file_store import LocalFileStore

STATE = Path('/tmp/viio-upload-check.json')


def text_pdf():
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    stream = b'BT /F1 14 Tf 50 700 Td (VIIO upload verification document.) Tj ET'
    objects.append(b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream')
    result = b'%PDF-1.4\n'
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'
    start = len(result)
    result += b'xref\n0 6\n0000000000 65535 f \n'
    result += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return result + f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode()


async def main(mode):
    settings = Settings()
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8000', timeout=30,
                                headers={'X-Internal-Token': settings.internal_api_key}) as client:
        if mode == 'begin':
            token = uuid.uuid4().hex
            files = [('files', ('Проверка-' + token + '.txt', 'Тестовый документ загрузки VIIO.'.encode(), 'text/plain')),
                ('files', (token + '.md', b'# VIIO test\nMarkdown upload fixture.', 'text/markdown')),
                ('files', (token + '.pdf', text_pdf(), 'application/pdf')),
                ('files', (token + '-cancel.pdf', text_pdf(), 'application/pdf')),
                ('files', (token + '-broken.pdf', b'%PDF-invalid', 'application/pdf')),
                ('files', ('invalid.exe', b'invalid', 'application/octet-stream'))]
            before = (await client.get('/api/v1/documents/catalog')).json()['total']
            response = await client.post('/api/v1/documents/bulk-upload', files=files,
                params={'owner_id': 'viio-upload-check-' + token, 'allowed_roles': 'admin', 'allowed_departments': ''})
            response.raise_for_status()
            state = {'tasks': response.json()['tasks'], 'before': before, 'token': token}
            STATE.write_text(json.dumps(state))
            assert state['tasks'][-1]['status'] == 'error'
            print(json.dumps({'stage': 'accepted', 'accepted': 5, 'rejected': 1}))
        elif mode == 'prepared':
            state = json.loads(STATE.read_text())
            identifiers = [task['task_id'] for task in state['tasks'] if task['task_id']]
            async with asyncio.timeout(180):
                while True:
                    response = await client.get('/api/v1/documents/bulk-status', params={'task_ids': ','.join(identifiers)})
                    response.raise_for_status()
                    tasks = response.json()['tasks']
                    if sum(task['status'] == 'needs_review' for task in tasks) == 2:
                        break
                    await asyncio.sleep(1)
            assert all(task['document_id'] is None for task in tasks if task['status'] == 'needs_review')
            print(json.dumps({'stage': 'prepared', 'reviews_waiting': 2}))
        elif mode == 'verify':
            state = json.loads(STATE.read_text())
            identifiers = [task['task_id'] for task in state['tasks'] if task['task_id']]
            async with asyncio.timeout(180):
                while True:
                    response = await client.get('/api/v1/documents/bulk-status', params={'task_ids': ','.join(identifiers)})
                    response.raise_for_status()
                    tasks = response.json()['tasks']
                    for task in tasks:
                        if task['status'] != 'needs_review':
                            continue
                        if task['filename'].endswith('-cancel.pdf'):
                            cancelled = await client.post('/api/v1/documents/bulk-cancel/' + task['task_id'])
                            cancelled.raise_for_status()
                            continue
                        review = await client.get('/api/v1/documents/bulk-review/' + task['task_id'])
                        review.raise_for_status()
                        preview = review.json()
                        assert preview['pages'] == 1 and 'VIIO upload' in preview['content']
                        approved = await client.post('/api/v1/documents/bulk-review/' + task['task_id'],
                            params={'reviewer_id': 'viio-upload-check-' + state['token']},
                            json={'content': preview['content'] + '\nChecked by fixture.', 'review_token': preview['token']})
                        approved.raise_for_status()
                    if len(tasks) == 5 and all(task['status'] in {'ready', 'error', 'cancelled'} for task in tasks):
                        break
                    await asyncio.sleep(1)
            assert sum(task['status'] == 'ready' for task in tasks) == 3, tasks
            assert sum(task['status'] == 'cancelled' for task in tasks) == 1
            for task in tasks:
                if task['status'] != 'ready':
                    continue
                response = await client.get('/api/v1/documents/download/' + task['document_id'], params={'user_role': 'admin'})
                response.raise_for_status()
                if task['filename'].endswith('.pdf'):
                    assert response.content == text_pdf()
                elif task['filename'].endswith('.txt'):
                    assert response.content == 'Тестовый документ загрузки VIIO.'.encode()
                    assert "filename*=UTF-8''" in response.headers['content-disposition']
                denied = await client.get('/api/v1/documents/download/' + task['document_id'], params={'user_role': 'employee'})
                assert denied.status_code == 403
            print(json.dumps({'stage': 'verified_after_restart', 'ready': 3, 'failed_pdf': 1, 'cancelled_pdf': 1,
                              'review_approval': 'passed', 'originals_and_acl': 'passed'}))
        elif mode == 'cleanup':
            state = json.loads(STATE.read_text())
            ids = [task['task_id'] for task in state['tasks'] if task['task_id']]
            if state.get('version_task'):
                ids.append(state['version_task'])
            dsn = make_conninfo(host=settings.postgres_host, port=settings.postgres_port, dbname=settings.postgres_db,
                user=settings.postgres_user, password=settings.postgres_password)
            store = LocalFileStore(settings.documents_storage_path)
            vector = QdrantClient(url=settings.qdrant_url)
            async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
                rows = await (await conn.execute('SELECT * FROM document_upload_jobs WHERE id=ANY(%s)', (ids,))).fetchall()
                for row in rows:
                    if row['document_id']:
                        vector.delete(settings.qdrant_collection_name, models.FilterSelector(filter=models.Filter(must=[
                            models.FieldCondition(key='metadata.document_id', match=models.MatchValue(value=row['document_id']))])), wait=True)
                        await conn.execute('DELETE FROM documents WHERE id=%s AND owner_id=%s',
                            (row['document_id'], 'viio-upload-check-' + state['token']))
                    store.delete(row['payload']['storage_key'])
                await conn.execute('DELETE FROM document_upload_jobs WHERE id=ANY(%s)', (ids,))
            vector.close()
            print(json.dumps({'stage': 'cleanup', 'fixture_jobs_removed': len(rows)}))
        elif mode == 'version':
            state = json.loads(STATE.read_text())
            original_task = next(task for task in state['tasks'] if task['filename'].endswith('.txt'))
            response = await client.get('/api/v1/documents/bulk-status', params={'task_ids': original_task['task_id']})
            response.raise_for_status()
            state['version_parent'] = response.json()['tasks'][0]['document_id']
            response = await client.post('/api/v1/documents/bulk-upload',
                params={'version_parent_id': state['version_parent'], 'is_public': True, 'allowed_roles': 'employee'},
                files=[('files', ('replacement.txt', b'Updated version fixture.', 'text/plain'))])
            response.raise_for_status()
            state['version_task'] = response.json()['tasks'][0]['task_id']
            STATE.write_text(json.dumps(state))
            async with asyncio.timeout(180):
                while True:
                    response = await client.get('/api/v1/documents/bulk-status', params={'task_ids': state['version_task']})
                    response.raise_for_status()
                    task = response.json()['tasks'][0]
                    if task['status'] in {'ready', 'error'}:
                        break
                    await asyncio.sleep(1)
            assert task['status'] == 'ready', task
            print(json.dumps({'stage': 'version_created', 'version': 2}))
        elif mode == 'verify-version':
            state = json.loads(STATE.read_text())
            response = await client.get('/api/v1/documents/versions/' + state['version_parent'])
            response.raise_for_status()
            versions = response.json()['items']
            assert [item['version'] for item in versions] == [2, 1]
            assert [item['is_active'] for item in versions] == [True, False]
            assert all(not item['is_public'] and item['allowed_roles'] == ['admin'] for item in versions)
            for item, expected in zip(versions, [b'Updated version fixture.', 'Тестовый документ загрузки VIIO.'.encode()]):
                original = await client.get('/api/v1/documents/download/' + item['id'], params={'user_role': 'admin'})
                assert original.status_code == 200 and original.content == expected
            from assistent.models import create_embeddings
            from assistent.utils.vector_store import QdrantVectorStoreAdapter
            vector = QdrantClient(url=settings.qdrant_url)
            try:
                adapter = QdrantVectorStoreAdapter(vector, create_embeddings(settings), settings.qdrant_collection_name)
                matches = adapter.similarity_search('version fixture', k=10, filter=models.Filter(must=[
                    models.FieldCondition(key='metadata.document_id', match=models.MatchAny(any=[item['id'] for item in versions]))]))
                assert matches and {doc.metadata['document_id'] for doc in matches} == {versions[0]['id']}
            finally:
                vector.close()
            print(json.dumps({'stage': 'versions_verified_after_restart', 'history': 2,
                              'originals': 'passed', 'access_inherited': 'passed', 'search_current_only': 'passed'}))


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1]))
