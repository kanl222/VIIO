import pytest
from unittest.mock import MagicMock
from langchain_core.documents import Document

from assistent.services.answer_sources import (
    extract_section_path,
    readable_reference,
    reference_title,
)
from viio_rag.search_tool import format_documents


def test_extract_section_path():
    # 1. From XML attribute section="..."
    tag = '<context source="Инструкция" doc_id="1" section="Приёмка товара > Сканирование упаковок">'
    assert extract_section_path(tag) == "Приёмка товара > Сканирование упаковок"

    # 2. From [Раздел: ...] prefix in body
    body_with_prefix = "[Раздел: 3. Маркировка товара > 3.2. Вывод из оборота]\nШаг 1. Отсканируйте код."
    assert extract_section_path(body_with_prefix) == "3. Маркировка товара > 3.2. Вывод из оборота"

    # 3. From Markdown headings in body
    markdown_body = "## 4. Списание брака\n### 4.1. Оформление акта\nТекст регламента..."
    assert extract_section_path(markdown_body) == "4. Списание брака > 4.1. Оформление акта"


def test_readable_reference_with_hierarchical_breadcrumbs():
    reference = (
        '<context source="06_is_lo_rukovodstvo_polzovatelya" doc_id="doc-guide" '
        'section="Руководство пользователя ИС ЛО > 3. Приёмка товара > 3.1. Сканирование">\n'
        '# Руководство пользователя ИС ЛО\n'
        'Шаг 1: Нажмите кнопку F5.\n'
        '</context>'
    )
    result = readable_reference(reference)

    assert "Материал «Руководство пользователя ИС ЛО» [Раздел: 3. Приёмка товара > 3.1. Сканирование]:" in result
    assert "Шаг 1: Нажмите кнопку F5." in result


def test_format_documents_with_section_path():
    doc = Document(
        page_content="Шаг 2. Заполните реквизиты накладной.",
        metadata={
            "title": "Регламент работы склада",
            "document_id": "doc-wh",
            "section_path": ["Складской учёт", "Оприходование"],
        },
    )
    formatted = format_documents([doc])

    assert 'section="Складской учёт &gt; Оприходование"' in formatted
    assert "[Раздел: Складской учёт > Оприходование]" in formatted
    assert "Шаг 2. Заполните реквизиты накладной." in formatted
