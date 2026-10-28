from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager
import pytest
from langchain_core.documents import Document
from viio_documents.services.enhanced_retrieval import EnhancedRetrieval
from viio_documents.services.knowledge_data import KnowledgeData


@pytest.mark.asyncio
async def test_diagnostics_do_not_export_denied_candidates_or_change_selection():
    allowed = Document(page_content='invoice instructions', metadata={'document_id':'allowed','title':'Invoices'})
    denied = Document(page_content='PRIVATE_CANARY', metadata={'document_id':'denied','title':'Private'})
    vector = SimpleNamespace(similarity_search=lambda **_: [denied, allowed])
    data = SimpleNamespace(lexical=AsyncMock(return_value=[allowed]), public_index=None,
                           authorized_candidates=AsyncMock(return_value=[allowed]))
    retrieval = EnhancedRetrieval(vector, data)
    diagnostic = {}
    result = await retrieval.search('invoice', {'role':'employee'}, 1, diagnostics=diagnostic)
    baseline = await retrieval.search('invoice', {'role':'employee'}, 1)
    assert result == baseline == [allowed]
    assert diagnostic['candidates'][0]['document_id'] == 'allowed'
    assert diagnostic['selected'][0]['text'] == 'invoice instructions'
    assert 'PRIVATE_CANARY' not in str(diagnostic)


@pytest.mark.asyncio
async def test_public_diagnostics_failure_does_not_break_successful_search():
    cursor = SimpleNamespace(fetchall=AsyncMock(return_value=[{'id':'approved', 'title':'Guide', 'content':'approved text'}]))
    connection = SimpleNamespace(execute=AsyncMock(return_value=cursor))
    @asynccontextmanager
    async def connect():
        yield connection
    data = KnowledgeData(SimpleNamespace(pool=SimpleNamespace(connection=connect)), public_index=object())
    data.public_vectors = AsyncMock(return_value=[Document(page_content='approved text', metadata={'publication_id':'approved'})])
    data.public_ids = AsyncMock(side_effect=RuntimeError('diagnostics check unavailable'))
    diagnostic = {}
    result = await data.public_search('Guide', diagnostics=diagnostic)
    assert result[0]['content'] == 'approved text'
    assert diagnostic == {'diagnostic_status':'unavailable'}
