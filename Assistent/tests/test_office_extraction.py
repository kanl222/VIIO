from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED

import pytest
from assistent.config import Settings
from assistent.services.office_extraction import OfficeExtractionService, OfficeExtractionError
from assistent.services.text_normalization import normalize_document_text
from office_samples import office_sample


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix',['.docx','.odt'])
async def test_real_office_structure_and_normalized_meaning(suffix):
    original=office_sample(suffix)
    result=await OfficeExtractionService(Settings()).extract(original,'Правила'+suffix.upper())
    text=result['content']
    assert 'Порядок работы' in text
    assert 'Контрольная фраза договор № 42. Сумма 1 000,50 ₽.' in text
    assert '- Первый пункт' in text and 'Срок | 14 дней' in text
    assert text.index('Контрольная')<text.index('Поле | Значение')<text.index('После таблицы.')
    assert '\u00ad' not in text and '\u200b' not in text and '\t' not in text
    assert '\n\n\n' not in text
    assert result['pages'] is None  # Page count cannot be inferred without layout rendering.


def test_normalizer_removes_noise_without_removing_meaning_or_code_indentation():
    text='\ufeff  Заголовок   \r\n\r\n\r\n  Пункт\u00a0\u00a01.2 — 10\u202f000 ₽; email: a@example.ru.\u200b\x01\r\n\tОбычный   абзац\r\n  - Вложенный пункт\r\n```python\n    print(  "a b"  )\n```'
    cleaned=normalize_document_text(text)
    assert cleaned.startswith('Заголовок\n\nПункт 1.2 — 10 000 ₽; email: a@example.ru.\nОбычный абзац')
    assert '\n  - Вложенный пункт' in cleaned
    assert '    print(  "a b"  )' in cleaned
    assert normalize_document_text(cleaned)==cleaned


def test_old_imported_pdf_title_page_has_no_layout_whitespace():
    text = '[Страница 1]\n  \n1 \n \n \n \nИнструкция для медицинских организаций  \nпо работе в ИС «Аптечный учёт в МО» \nмодуль «Аптека»   \n \n \nДата актуализации 23.05.2022 \n  \n\n[Страница 2]\n  \n2 '
    cleaned = normalize_document_text(text)
    assert '\n\n\n' not in cleaned
    assert all(not line or line == line.strip() for line in cleaned.splitlines())
    assert '[Страница 1]\n\n1\n\nИнструкция для медицинских организаций' in cleaned
    assert 'Дата актуализации 23.05.2022\n\n[Страница 2]\n\n2' in cleaned
    assert ''.join(text.split()) == ''.join(cleaned.split())


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix',['.docx','.odt'])
async def test_rejects_corrupt_packages_and_empty_text(suffix):
    extractor=OfficeExtractionService(Settings())
    for value in [b'PK\x03\x04broken',b'not office']:
        with pytest.raises(OfficeExtractionError):await extractor.extract(value,'file'+suffix)
    if suffix=='.docx':
        from docx import Document
        doc=Document();doc.add_table(rows=1,cols=2);target=BytesIO();doc.save(target)
    else:
        from odf.opendocument import OpenDocumentText
        from odf.table import Table,TableRow,TableCell
        doc=OpenDocumentText();grid=Table();row=TableRow();row.addElement(TableCell());row.addElement(TableCell());grid.addElement(row);doc.text.addElement(grid)
        target=BytesIO();doc.save(target)
    with pytest.raises(OfficeExtractionError,match='нет извлекаемого текста'):
        await extractor.extract(target.getvalue(),'file'+suffix)


@pytest.mark.asyncio
async def test_limits_file_size_text_size_and_rejects_format_mismatch():
    doc=office_sample('.docx')
    with pytest.raises(OfficeExtractionError,match='лимит'):
        await OfficeExtractionService(Settings(bulk_max_file_bytes=1024)).extract(doc,'file.docx')
    with pytest.raises(OfficeExtractionError):
        await OfficeExtractionService(Settings()).extract(doc,'file.odt')
    with pytest.raises(OfficeExtractionError,match='много текста'):
        await OfficeExtractionService(Settings(pdf_max_chars=100)).extract(doc,'file.docx')


def replace_member(data,member,content):
    target=BytesIO()
    with ZipFile(BytesIO(data)) as source,ZipFile(target,'w',ZIP_DEFLATED) as dest:
        for item in source.infolist():dest.writestr(item,content if item.filename==member else source.read(item))
    return target.getvalue()


@pytest.mark.asyncio
async def test_xml_entities_and_whitespace_expansion_are_rejected():
    data=office_sample('.odt')
    malicious=b'<!DOCTYPE foo [<!ENTITY leak SYSTEM "file:///etc/passwd">]><foo>&leak;</foo>'
    with pytest.raises(OfficeExtractionError):
        await OfficeExtractionService(Settings()).extract(replace_member(data,'content.xml',malicious),'file.odt')
    with ZipFile(BytesIO(data)) as archive:xml=archive.read('content.xml')
    repeated=xml.replace(b'<text:p>',b'<text:p><text:s text:c="999999999999"/>',1)
    with pytest.raises(OfficeExtractionError):
        await OfficeExtractionService(Settings()).extract(replace_member(data,'content.xml',repeated),'file.odt')


@pytest.mark.asyncio
async def test_expanded_archive_size_is_bounded():
    target=BytesIO()
    with ZipFile(target,'w',ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml',b'x'*(51*1024*1024))
    with pytest.raises(OfficeExtractionError,match='распаковки'):
        await OfficeExtractionService(Settings()).extract(target.getvalue(),'file.docx')


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix',['.docx','.odt'])
async def test_hyperlink_address_is_not_lost_when_label_differs(suffix):
    url='https://example.test/access?form=42'
    target=BytesIO()
    if suffix=='.docx':
        from docx import Document
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.opc.constants import RELATIONSHIP_TYPE
        doc=Document();paragraph=doc.add_paragraph('Документ: ')
        link=OxmlElement('w:hyperlink');link.set(qn('r:id'),doc.part.relate_to(url,RELATIONSHIP_TYPE.HYPERLINK,is_external=True))
        run=OxmlElement('w:r');text=OxmlElement('w:t');text.text='Форма доступа';run.append(text);link.append(run);paragraph._p.append(link)
        doc.save(target)
    else:
        from odf.opendocument import OpenDocumentText
        from odf.text import P,A
        doc=OpenDocumentText();paragraph=P(text='Документ: ');paragraph.addElement(A(href=url,text='Форма доступа'));doc.text.addElement(paragraph);doc.save(target)
    result=await OfficeExtractionService(Settings()).extract(target.getvalue(),'form'+suffix)
    assert url in result['content'] and 'Форма доступа' in result['content']
