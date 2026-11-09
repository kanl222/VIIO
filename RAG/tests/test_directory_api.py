from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from viio_rag.document_directory import search_selected_documents
from test_rag_api import rag_fixture, READ_KEY
from viio_documents.services.document_catalog import normalize_catalog_metadata


@pytest.mark.asyncio
async def test_selected_content_rechecks_acl_without_global_retrieval():
    base = dict(title='Guide', mime_type='text/plain', file_size=None, sha256=None,
                owner_id=None, version=1, parent_id=None, status='ready', is_public=False,
                allowed_roles=['admin'], allowed_departments=[], storage_key=None,
                chunks_count=1, created_at=None, updated_at=None)
    rows = [dict(base, id='private', content='SECRET'),
            dict(base, id='public', is_public=True, content='Public text')]
    conn = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchall=AsyncMock(return_value=rows))))

    @asynccontextmanager
    async def connection():
        yield conn

    data = SimpleNamespace(catalog=SimpleNamespace(pool=SimpleNamespace(connection=connection)))
    docs = await search_selected_documents(data, 'text', ['private', 'public'], {'role': 'employee'})
    assert [doc.metadata['document_id'] for doc in docs] == ['public']
    assert 'SECRET' not in str(docs)
    assert conn.execute.call_args.args[1] == (['private', 'public'],)


@pytest.mark.asyncio
async def test_directory_api_requires_read_key_and_validates_filters(monkeypatch):
    app, data = rag_fixture()
    listing = AsyncMock(return_value={'items': [], 'total': 0, 'next_offset': None, 'untagged_count': 0})
    monkeypatch.setattr('viio_rag.app.list_documents', listing)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://rag') as client:
        assert (await client.post('/api/v1/knowledge/documents', json={})).status_code == 401
        assert (await client.post('/api/v1/knowledge/documents', json={'limit': 1000},
                                  headers={'X-Internal-Token': READ_KEY})).status_code == 422
        response = await client.post('/api/v1/knowledge/documents', json={'tags': ['инструкция']},
                                     headers={'X-Internal-Token': READ_KEY})
        assert response.status_code == 200
        assert listing.call_args.args[1].tags == ['инструкция']
        data.lexical.assert_not_called()


def test_catalog_tags_are_normalized_and_bounded():
    assert normalize_catalog_metadata({'tags': [' Инструкция ', 'инструкция', 'ИС ЛО']}) == {
        'tags': ['инструкция', 'ис ло'], 'description': ''}
    with pytest.raises(ValueError):
        normalize_catalog_metadata({'tags': 'инструкция'})
