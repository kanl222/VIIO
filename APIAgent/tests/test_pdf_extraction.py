from io import BytesIO
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from apiagent import create_app
from apiagent.config import Settings
from apiagent.services.pdf_extraction import PdfExtractionService, PdfExtractionError


def pdf_bytes(text="Approval takes 3 working days.", pages=1, encrypted=False):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=300, height=200)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 12 Tf 20 100 Td ({text}) Tj ET'.encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    if encrypted:
        writer.encrypt('test-password')
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.asyncio
async def test_real_text_pdf_extraction_is_preview_only():
    service = PdfExtractionService(Settings(_env_file=None))
    result = await service.extract(pdf_bytes())
    assert '3 working days' in result['content']
    assert result['pages'] == 1 and result['engine'] == 'pypdf'


@pytest.mark.asyncio
@pytest.mark.parametrize('data,status', [(b'not a pdf', 400), (b'%PDF-1.4 corrupt', 400)])
async def test_corrupt_or_non_pdf_is_rejected(data, status):
    with pytest.raises(PdfExtractionError) as failure:
        await PdfExtractionService(Settings(_env_file=None)).extract(data)
    assert failure.value.status == status


@pytest.mark.asyncio
async def test_encrypted_page_and_size_limits():
    cases = [(Settings(_env_file=None), pdf_bytes(encrypted=True), 400),
             (Settings(_env_file=None, pdf_max_pages=1), pdf_bytes(pages=2), 413),
             (Settings(_env_file=None, pdf_max_bytes=1024), b'%PDF-' + b'x'*1024, 413)]
    for settings, data, status in cases:
        with pytest.raises(PdfExtractionError) as failure:
            await PdfExtractionService(settings).extract(data)
        assert failure.value.status == status


@pytest.mark.asyncio
async def test_scanned_pdf_cannot_be_silently_indexed_as_empty_text():
    service = PdfExtractionService(Settings(_env_file=None, pdf_engine='pypdf'))
    with pytest.raises(PdfExtractionError) as failure:
        await service.extract(pdf_bytes(text=''))
    assert failure.value.status == 422


@pytest.mark.asyncio
async def test_docling_cpu_fallback_preserves_pdf_bytes_and_auth(monkeypatch):
    data = pdf_bytes(text='')
    settings = Settings(_env_file=None, internal_api_key='unit-secret')
    real_client = httpx.AsyncClient
    async def handler(request):
        assert request.url.path == '/extract'
        assert await request.aread() == data
        assert request.headers['X-Internal-Token'] == 'unit-secret'
        return httpx.Response(200, json={'content':'Recognized scan text'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    result = await PdfExtractionService(settings).extract(data)
    assert result['engine'] == 'docling-cpu' and result['content'] == 'Recognized scan text'


def test_pdf_endpoint_requires_internal_token_and_never_indexes():
    documents = MagicMock()
    client = TestClient(create_app(Settings(_env_file=None, internal_api_key='unit-secret'), MagicMock(), documents))
    path = '/api/v1/documents/extract-pdf'
    assert client.post(path, content=pdf_bytes(), headers={'Content-Type':'application/pdf'}).status_code == 401
    result = client.post(path, content=pdf_bytes(), headers={'Content-Type':'application/pdf','X-Internal-Token':'unit-secret'})
    assert result.status_code == 200 and '3 working days' in result.json()['content']
    documents.index_document.assert_not_called()


def test_pdf_endpoint_validates_content_type_and_stream_size():
    client = TestClient(create_app(Settings(_env_file=None, pdf_max_bytes=1024), MagicMock(), MagicMock()))
    assert client.post('/api/v1/documents/extract-pdf', content=b'x').status_code == 415
    result = client.post('/api/v1/documents/extract-pdf', content=b'%PDF-' + b'x'*1024, headers={'Content-Type':'application/pdf'})
    assert result.status_code == 413
