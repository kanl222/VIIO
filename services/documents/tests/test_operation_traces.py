import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from test_pdf_extraction import pdf_bytes
from viio_documents.config import Settings
from viio_rag.application.retrieval import EnhancedRetrieval
from viio_documents.infrastructure.extraction.pdf_extraction import PdfExtractionError, PdfExtractionService


def read_trace(directory):
    return json.loads(next(directory.glob('*.json')).read_text(encoding='utf-8'))


@pytest.mark.asyncio
async def test_pdf_trace_pairs_request_and_result_without_original_or_keys(tmp_path, monkeypatch):
    monkeypatch.setenv('VIIO_TRACE_DIR', str(tmp_path))
    monkeypatch.setenv('INTERNAL_API_KEY', 'DO_NOT_CAPTURE_THIS_KEY')
    service = PdfExtractionService(Settings(_env_file=None, pdf_engine='pypdf'), filename='guide.pdf', job_id='upload-42')
    result = await service.extract(pdf_bytes())
    trace = read_trace(tmp_path)
    assert trace['agent'] == 'ocr' and trace['question'] == 'guide.pdf'
    assert trace['thread_id'] == 'upload-42' and trace['status'] == 'ok'
    request, response = trace['events']
    assert request['stage'] == 'ocr_request' and request['size_bytes'] > 0
    assert len(request['sha256']) == 64
    assert response['stage'] == 'ocr_result' and response['content'] == result['content']
    assert 'DO_NOT_CAPTURE_THIS_KEY' not in json.dumps(trace)
    assert '%PDF-' not in json.dumps(trace)


@pytest.mark.asyncio
async def test_pdf_failure_keeps_request_and_safe_error(tmp_path, monkeypatch):
    monkeypatch.setenv('VIIO_TRACE_DIR', str(tmp_path))
    with pytest.raises(PdfExtractionError):
        await PdfExtractionService(Settings(_env_file=None)).extract(b'broken')
    trace = read_trace(tmp_path)
    assert trace['status'] == 'error'
    assert trace['events'][0]['stage'] == 'ocr_request'
    assert trace['events'][1]['status_code'] == 400


@pytest.mark.asyncio
async def test_search_trace_records_only_authorized_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv('VIIO_TRACE_DIR', str(tmp_path))
    allowed = Document(page_content='Allowed instructions', metadata={'document_id': 'allowed', 'title': 'Guide'})
    denied = Document(page_content='DENIED_PRIVATE_TEXT', metadata={'document_id': 'denied'})
    vector = SimpleNamespace(similarity_search=lambda **_: [denied, allowed])
    data = SimpleNamespace(lexical=AsyncMock(return_value=[allowed]), public_index=None,
                           authorized_candidates=AsyncMock(return_value=[allowed]))
    result = await EnhancedRetrieval(vector, data).search('instructions', {'role': 'employee'}, 1)
    trace = read_trace(tmp_path)
    assert result == [allowed] and trace['agent'] == 'search'
    assert [event['stage'] for event in trace['events']] == ['search_request', 'retrieval']
    assert trace['events'][1]['selected'][0]['document_id'] == 'allowed'
    assert 'DENIED_PRIVATE_TEXT' not in json.dumps(trace)


@pytest.mark.asyncio
async def test_search_error_is_traced_without_exception_details(tmp_path, monkeypatch):
    monkeypatch.setenv('VIIO_TRACE_DIR', str(tmp_path))
    def fail(**kwargs):
        raise RuntimeError('SECRET_DATABASE_DETAILS')
    data = SimpleNamespace(lexical=AsyncMock(side_effect=RuntimeError('SECRET_DATABASE_DETAILS')), public_index=None)
    with pytest.raises(RuntimeError):
        await EnhancedRetrieval(SimpleNamespace(similarity_search=fail), data).search('query', None, 1)
    trace = read_trace(tmp_path)
    assert trace['status'] == 'error' and trace['events'][1]['status'] == 'error'
    assert 'SECRET_DATABASE_DETAILS' not in json.dumps(trace)


@pytest.mark.asyncio
async def test_unwritable_diagnostics_does_not_break_pdf(tmp_path, monkeypatch):
    target = tmp_path / 'file'
    target.write_text('not a directory')
    monkeypatch.setenv('VIIO_TRACE_DIR', str(target))
    result = await PdfExtractionService(Settings(_env_file=None, pdf_engine='pypdf')).extract(pdf_bytes())
    assert result['pages'] == 1
