from io import BytesIO

import pytest

from viio_documents.config import Settings
from viio_rag.chunks import document_chunks
from viio_documents.services.office_extraction import OfficeExtractionService


def test_adjacent_empty_section_is_not_dropped():
    parts = list(document_chunks('# Guide\n## Empty section\n## Next\nBody', 200, 20))
    assert any(meta['section_path'] == ['Guide', 'Empty section'] for _, meta in parts)
    assert any('Body' in text for text, _ in parts)


def test_fenced_pipe_lines_are_not_reformatted_as_a_table():
    content = '# Example\n\n```text\nintro\n| a | b |\n| --- | --- |\n| 1 | 2 |\nend\n```'
    parts = list(document_chunks(content, 500, 20))
    assert len(parts) == 1
    assert parts[0][0] == content


def test_table_cells_preserve_literal_pipes_empty_columns_and_line_breaks():
    from viio_documents.services.office_extraction import markdown_table
    table = markdown_table([['Name', 'Value'], ['A | B', 'first\nsecond'], ['', '42']])
    assert '| A &#124; B | first<br>second |' in table
    assert '|  | 42 |' in table
    assert len(table.splitlines()) == 4
    assert markdown_table([['', '']]) == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix', ['.docx', '.odt'])
async def test_office_tables_keep_column_context_in_continuation(suffix):
    rows = [('Medicine', 'Quantity')] + [(f'Medicine{i}', f'{i} packages') for i in range(30)]
    output = BytesIO()
    if suffix == '.docx':
        from docx import Document
        doc = Document()
        doc.add_heading('Inventory', 1)
        table = doc.add_table(rows=len(rows), cols=2)
        for index, values in enumerate(rows):
            for column, value in enumerate(values):
                table.cell(index, column).text = value
        doc.save(output)
    else:
        from odf.opendocument import OpenDocumentText
        from odf.table import Table, TableRow, TableCell
        from odf.text import H, P
        doc = OpenDocumentText()
        doc.text.addElement(H(outlinelevel=1, text='Inventory'))
        table = Table()
        for values in rows:
            row = TableRow()
            for value in values:
                cell = TableCell()
                cell.addElement(P(text=value))
                row.addElement(cell)
            table.addElement(row)
        doc.text.addElement(table)
        doc.save(output)
    extracted = await OfficeExtractionService(Settings(_env_file=None)).extract(output.getvalue(), 'inventory' + suffix)
    parts = list(document_chunks(extracted['content'], 180, 20))
    table_parts = [text for text, _ in parts if 'packages' in text]
    assert len(table_parts) > 1
    assert all('| Medicine | Quantity |' in text for text in table_parts)
    assert all(any(f'| Medicine{i} | {i} packages |' in text for text in table_parts) for i in range(30))
    assert all(len(text) <= 180 for text, _ in parts)

def test_table_of_contents_is_stripped_from_chunks():
    content = '''# Инструкция ИС ЛО
Оглавление
1. Раздел один........................................................................... 3
1.1. Подраздел........................................................................... 5
2. Раздел два........................................................................... 15

## 1. Раздел один
Текст первого раздела с подробными инструкциями.

## 2. Раздел два
Инструкция по вводу начальных остатков на складе.'''
    parts = list(document_chunks(content, 500, 50))
    # Ни один фрагмент не должен содержать строк оглавления с точками
    assert not any('................' in text for text, _ in parts)
    assert not any(meta['section_path'] == ['Инструкция ИС ЛО', 'Оглавление'] for _, meta in parts)
    # Настоящие разделы сохранены
    assert any('Инструкция по вводу начальных остатков' in text for text, _ in parts)
    assert any(meta['section_path'] == ['Инструкция ИС ЛО', '2. Раздел два'] for _, meta in parts)


def test_numbered_sections_without_markdown_hashes():
    content = '''# Руководство
1. Основные настройки
Здесь настраиваются системные параметры.

1.1. Параметры доступа
Инструкция по выдаче прав пользователям.'''
    parts = list(document_chunks(content, 500, 50))
    paths = [meta['section_path'] for _, meta in parts]
    assert any('1. Основные настройки' in path for path in paths)
    assert any('1.1. Параметры доступа' in path for path in paths)

