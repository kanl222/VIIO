"""Bounded CPU extraction of DOCX/ODT paragraphs, lists and tables."""
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import anyio.to_thread
from defusedxml import ElementTree
from docx import Document
from docx.table import Table
from docx.text.hyperlink import Hyperlink
from odf.opendocument import load
from odf import teletype
from odf.text import A

from assistent.services.text_normalization import normalize_document_text

OFFICE_MIMES = {
    '.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.odt':'application/vnd.oasis.opendocument.text',
}
TEXT_NS = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
TABLE_NS = 'urn:oasis:names:tc:opendocument:xmlns:table:1.0'


class OfficeExtractionError(ValueError):
    """Safe user-facing reason; never return XML/parser internals."""


def validate_package(data, suffix, max_chars):
    try:
        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(item.file_size for item in entries) > 50 * 1024 * 1024:
                raise OfficeExtractionError('Документ слишком велик после распаковки. Разделите его на части.')
            if len({item.filename for item in entries}) != len(entries):
                raise OfficeExtractionError('В документе повторяются внутренние файлы. Сохраните его заново.')
            for item in entries:
                if item.flag_bits & 1:
                    raise OfficeExtractionError('Документ защищён паролем. Сохраните незашифрованную копию.')
                if not item.filename.endswith(('.xml', '.rels')):
                    continue
                root = ElementTree.fromstring(archive.read(item), forbid_dtd=True)
                count, estimated_text, pending = 0, 0, [(root, 0)]
                while pending:
                    node, depth = pending.pop()
                    count += 1
                    if suffix == '.odt' and item.filename == 'content.xml':
                        estimated_text += len(node.text or '') + len(node.tail or '')
                    if depth > 100 or count > 200000:
                        raise OfficeExtractionError('Слишком сложная структура документа. Разделите его на части.')
                    for key, value in node.attrib.items():
                        if key in {f'{{{TEXT_NS}}}c', f'{{{TABLE_NS}}}number-columns-repeated', f'{{{TABLE_NS}}}number-rows-repeated'}:
                            if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 10000:
                                raise OfficeExtractionError('Некорректные повторы пробелов или таблиц в документе.')
                            if key == f'{{{TEXT_NS}}}c':
                                estimated_text += int(value)
                    if suffix == '.odt' and item.filename == 'content.xml' and estimated_text > max_chars:
                        raise OfficeExtractionError('В документе слишком много текста или пробелов. Разделите его.')
                    pending.extend((child, depth + 1) for child in node)
            if suffix == '.odt':
                if archive.read('mimetype').decode('ascii').strip() != OFFICE_MIMES[suffix]:
                    raise OfficeExtractionError('Файл не является текстовым документом ODT.')
            elif not {'word/document.xml', '[Content_Types].xml'} <= set(archive.namelist()):
                raise OfficeExtractionError('Файл не является документом DOCX.')
    except OfficeExtractionError:
        raise
    except Exception:
        raise OfficeExtractionError('Не удалось прочитать документ. Проверьте формат, целостность и отсутствие пароля.') from None


def docx_blocks(container):
    for block in container.iter_inner_content():
        if isinstance(block, Table):
            for row in block.rows:
                seen, cells = set(), []
                for cell in row.cells:
                    # A merged grid cell can occur several times in row.cells.
                    if cell._tc in seen:
                        continue
                    seen.add(cell._tc)
                    cells.append(' / '.join(docx_blocks(cell)).strip())
                yield ' | '.join(cells)
        else:
            parts = []
            for item in block.iter_inner_content():
                value = item.text
                if isinstance(item, Hyperlink) and item.url and item.url not in value:
                    value += ' (' + item.url + ')'
                parts.append(value)
            text = ''.join(parts)
            style = block.style.name.lower() if block.style else ''
            numbering = block._p.pPr.numPr if block._p.pPr is not None else None
            if text.strip() and (numbering is not None or style.startswith('list')):
                level = numbering.ilvl.val if numbering is not None and numbering.ilvl is not None else 0
                text = '  ' * min(level, 8) + '- ' + text
            yield text


def odt_blocks(node, depth=0, max_chars=500000):
    for child in node.childNodes:
        name = getattr(child, 'qname', None)
        if not name:
            continue
        namespace, tag = name
        if namespace == TEXT_NS and tag in {'p', 'h'}:
            text = teletype.extractText(child)
            links = list(dict.fromkeys(link.getAttribute('href') for link in child.getElementsByType(A)
                if link.getAttribute('href') and link.getAttribute('href') not in text))
            yield text + (' (' + '; '.join(links) + ')' if links else '')
        elif namespace == TABLE_NS and tag == 'table':
            yield from odt_table(child, max_chars)
        elif namespace == TEXT_NS and tag == 'list':
            for item in child.childNodes:
                if getattr(item, 'qname', None) != (TEXT_NS, 'list-item'):
                    continue
                first = True
                for line in odt_blocks(item, depth + 1, max_chars):
                    if first:
                        yield '  ' * min(depth, 8) + '- ' + line
                        first = False
                    else:
                        yield line
        elif tag not in {'annotation', 'tracked-changes', 'change-info', 'scripts'}:
            yield from odt_blocks(child, depth, max_chars)


def odt_table(table, max_chars):
    def rows(node):
        for child in node.childNodes:
            if getattr(child, 'qname', None) == (TABLE_NS, 'table-row'):
                yield child
            elif getattr(child, 'qname', None) in {(TABLE_NS, 'table-header-rows'), (TABLE_NS, 'table-rows'), (TABLE_NS, 'table-row-group')}:
                yield from rows(child)
    emitted, characters = 0, 0
    for row in rows(table):
        cells, row_size = [], 0
        for cell in row.childNodes:
            if getattr(cell, 'qname', None) not in {(TABLE_NS, 'table-cell'),(TABLE_NS, 'covered-table-cell')}:
                continue
            repeat = int(cell.getAttribute('numbercolumnsrepeated') or 1)
            value = ' / '.join(odt_blocks(cell, max_chars=max_chars)).strip()
            row_size += (len(value) + 3) * repeat
            if row_size > max_chars:
                raise OfficeExtractionError('Слишком много текста в строке таблицы. Разделите документ.')
            if len(cells) + repeat > 10000:
                raise OfficeExtractionError('Слишком широкая таблица. Разделите документ.')
            cells.extend([value] * repeat)
        repeated_rows = int(row.getAttribute('numberrowsrepeated') or 1)
        emitted += repeated_rows
        characters += row_size * repeated_rows
        if emitted > 10000 or characters > max_chars:
            raise OfficeExtractionError('Слишком длинная таблица. Разделите документ.')
        yield from [' | '.join(cells)] * repeated_rows


class OfficeExtractionService:
    def __init__(self, settings):
        self.settings = settings

    def _read(self, data, filename):
        suffix = Path(filename).suffix.lower()
        if suffix not in OFFICE_MIMES:
            raise OfficeExtractionError('Поддерживаются DOCX и ODT.')
        if not data or len(data) > self.settings.bulk_max_file_bytes:
            raise OfficeExtractionError('Файл пустой или превышает лимит загрузки.')
        validate_package(data, suffix, self.settings.pdf_max_chars)
        try:
            blocks = docx_blocks(Document(BytesIO(data))) if suffix == '.docx' else odt_blocks(load(BytesIO(data)).text, max_chars=self.settings.pdf_max_chars)
            result, count = [], 0
            for block in blocks:
                count += len(block) + 2
                if count > self.settings.pdf_max_chars:
                    raise OfficeExtractionError('В документе слишком много текста. Разделите его на части.')
                result.append(block)
            text = normalize_document_text('\n\n'.join(result))
            if not text:
                raise OfficeExtractionError('В документе нет извлекаемого текста. Для сканов используйте PDF с OCR.')
            return {'content':text,'engine':'python-docx' if suffix == '.docx' else 'odfpy','pages':None}
        except OfficeExtractionError:
            raise
        except Exception:
            raise OfficeExtractionError('Не удалось разобрать структуру DOCX/ODT. Сохраните документ заново.') from None

    async def extract(self, data, filename):
        return await anyio.to_thread.run_sync(self._read, data, filename)
