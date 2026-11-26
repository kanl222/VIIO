from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager
import pytest
from langchain_core.documents import Document
from viio_rag.application.retrieval import EnhancedRetrieval, rank_documents, fragment_snapshot
from viio_rag.application.knowledge_data import KnowledgeData, procedure_context


def test_candidate_cache_snapshot_preserves_parent_but_summary_omits_text():
    doc = Document(page_content='small instruction', metadata={
        'document_id': 'd', 'parent_context': 'complete procedure',
        'window_context': 'adjacent steps', 'chunker_version': 'markdown-procedure-v5'})
    cached = fragment_snapshot(doc)
    assert cached['parent_context'] == 'complete procedure'
    assert cached['window_context'] == 'adjacent steps'
    summary = fragment_snapshot(doc, include_text=False)
    assert not {'text', 'parent_context', 'window_context'} & summary.keys()


def test_parent_recovery_finds_late_procedure_in_large_manual():
    content = '# Руководство\n## Запуск\n' + ('Настройки приложения. ' * 800)
    content += '\n## Отпуск в отделение\nУправление запасами → Документы отпуска в отделение → Создать.\nВид цены: Закупочная.'
    parent = procedure_context(content, 'Как создать «Отпуск в отделение» и какой вид цены?', 1800)
    assert 'Вид цены: Закупочная.' in parent
    assert len(parent) <= 1800


@pytest.mark.asyncio
async def test_parent_recovery_never_loads_denied_or_publication_canonical_source():
    public = Document(page_content='approved snapshot', metadata={'document_id': 'private', 'publication_id': 'pub'})
    denied = Document(page_content='secret', metadata={'document_id': 'denied'})
    pool = SimpleNamespace(connection=AsyncMock(side_effect=AssertionError('must not load canonical source')))
    data = KnowledgeData(SimpleNamespace(pool=pool))
    data.authorized_candidates = AsyncMock(return_value=[public])
    assert await data.evidence_parents([public, denied], 'question', {'role': 'employee'}) == {}


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
    assert 'text' not in diagnostic['candidates'][0]
    assert diagnostic['selected'][0]['text'] == 'invoice instructions'
    assert diagnostic['ranking_details']['before_access_check'] == 2
    assert diagnostic['ranking_details']['after_access_check'] == 1
    assert diagnostic['candidates'][0]['branch_ranks'] == {'lexical': [1], 'semantic': [2]}
    assert 'PRIVATE_CANARY' not in str(diagnostic)
    assert 'denied' not in str(diagnostic)


@pytest.mark.asyncio
async def test_diagnostics_explain_title_bonus_ranks_and_threshold():
    pharmacy = Document(page_content='Поиск уже выданного рецепта',
                        metadata={'document_id': 'pharmacy', 'title': 'Поиск рецептов'})
    doctor = Document(page_content='Выписать и подписать рецепт',
                      metadata={'document_id': 'doctor', 'title': 'guide_is_lo', 'filename': 'guide.pdf'})
    noise = Document(page_content='Складской учёт', metadata={'document_id': 'noise'})
    data = SimpleNamespace(lexical=AsyncMock(return_value=[doctor, pharmacy]), public_index=object(),
                           public_vectors=AsyncMock(side_effect=TimeoutError('PRIVATE_UPSTREAM_DETAIL')),
                           authorized_candidates=AsyncMock(return_value=[doctor, pharmacy, noise]))
    vector = SimpleNamespace(similarity_search=lambda **_: [noise, doctor, pharmacy])
    diagnostic = {}

    result = await EnhancedRetrieval(vector, data, branch_weights={'semantic': 1.0, 'lexical': 1.0}).search(
        'как выписать рецепт', None, 2, diagnostics=diagnostic)

    assert result == [pharmacy, doctor]
    assert diagnostic['ranking_details']['query_terms'] == ['выписать', 'рецепт']
    scores = {item['document_id']: item for item in diagnostic['candidates']}
    assert scores['pharmacy']['matched_text'] == scores['pharmacy']['matched_title'] == ['рецепт']
    assert scores['pharmacy']['term_score'] == 8
    assert scores['pharmacy']['rrf_score'] == pytest.approx(1 / 62 + 1 / 63)
    assert scores['pharmacy']['score_rank'] == scores['pharmacy']['final_rank'] == 1
    assert scores['doctor']['matched_text'] == ['выписать', 'рецепт']
    assert scores['doctor']['term_score'] == 6 and scores['doctor']['final_rank'] == 2
    assert scores['noise']['score_rank'] == 3 and scores['noise']['final_rank'] is None
    assert diagnostic['branches']['public']['error'] == 'TimeoutError'
    assert 'PRIVATE_UPSTREAM_DETAIL' not in str(diagnostic)


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


@pytest.mark.asyncio
async def test_rank_documents_filters_zero_score_candidates():
    doc1 = Document(page_content="распоряжение 1035 рецепт", metadata={"document_id": "doc1", "title": "Схема"})
    doc2 = Document(page_content="руководство по аптечному учету", metadata={"document_id": "doc2", "title": "Аптека"})
    # Поиск по репозиторию или коду не должен возвращать нерелевантные документы
    result = rank_documents("github repository release commit", [doc1, doc2], 5, min_score=1)
    assert result == []

