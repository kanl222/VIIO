"""Тесты для сервиса индексации документов DocumentService."""

from typing import Any
import pytest
from viio_documents.application.document_service import DocumentService, split_document_text


class MockVectorStore:
    """Мок векторного хранилища для тестирования DocumentService."""

    def __init__(self) -> None:
        self.stored_texts: list[str] = []
        self.stored_metadatas: list[dict[str, Any]] = []

    def add_texts(
        self, texts: list[str], metadatas: list[dict[str, Any]] | None = None
    ) -> list[str]:
        """Сохранение текстов и метаданных в память мока."""
        self.stored_texts.extend(texts)
        if metadatas:
            self.stored_metadatas.extend(metadatas)
        return [f"id-{i}" for i in range(len(texts))]

    def similarity_search(self, query: str, k: int = 4) -> list[Any]:
        """Поиск похожих документов."""
        return []

    def count(self) -> int:
        """Подсчет количества документов в моке."""
        return len(self.stored_texts)


def test_index_document_success() -> None:
    """Проверка успешной индексации документа с нарезкой на чанки."""
    mock_store = MockVectorStore()
    service = DocumentService(vector_store=mock_store, chunk_size=100, chunk_overlap=20)

    # Длинный текст, который должен разбиться на несколько чанков
    content = "Часть первая регламента. " * 20
    doc_meta = service.index_document(
        title="Тестовый регламент",
        content=content,
        metadata={"source": "pytest", "author": "admin"},
    )

    assert doc_meta.title == "Тестовый регламент"
    assert doc_meta.status == "indexed"
    assert doc_meta.chunks_count > 1
    assert len(mock_store.stored_texts) == doc_meta.chunks_count
    assert len(mock_store.stored_metadatas) == doc_meta.chunks_count
    # Проверка проброса метаданных
    for meta in mock_store.stored_metadatas:
        assert meta["title"] == "Тестовый регламент"
        assert meta["source"] == "pytest"
        assert meta["author"] == "admin"
        assert meta["document_id"] == doc_meta.document_id


def test_index_document_empty_title_or_content_raises() -> None:
    """Проверка выброса исключения при пустом заголовке или содержимом."""
    mock_store = MockVectorStore()
    service = DocumentService(vector_store=mock_store)

    with pytest.raises(ValueError, match="Заголовок не может быть пустым"):
        service.index_document(title="", content="Некоторый контент")

    with pytest.raises(ValueError, match="Содержимое документа не может быть пустым"):
        service.index_document(title="Заголовок", content="   ")


def test_count_documents() -> None:
    """Проверка получения общего количества проиндексированных фрагментов."""
    mock_store = MockVectorStore()
    service = DocumentService(vector_store=mock_store)

    assert service.count_documents() == 0
    service.index_document(title="Док 1", content="Короткий текст")
    assert service.count_documents() >= 1


def test_word_chunks_preserve_overlap_and_line_breaks():
    content = ' '.join(f'слово{i}' for i in range(1200)).replace('слово10 ', 'слово10\n\n')
    chunks = split_document_text(content, 512, 100, 'words')
    assert [len(chunk.split()) for chunk in chunks] == [512, 512, 376]
    assert chunks[0].split()[-100:] == chunks[1].split()[:100]
    assert '\n\n' in chunks[0]
    assert chunks[-1].endswith('слово1199')


@pytest.mark.parametrize('size,overlap', [(0, 0), (10, 10), (10, -1)])
def test_invalid_chunk_settings_are_rejected(size, overlap):
    with pytest.raises(ValueError):
        split_document_text('Текст', size, overlap, 'words')


def test_markdown_sections_are_not_mixed_and_retain_heading_on_continuation():
    store = MockVectorStore()
    service = DocumentService(store, chunk_size=120, chunk_overlap=15)
    content = '# Guide\n\n## Initial balances\n\n' + 'Create inventory and save all balances. ' * 12 + '\n\n## Reorganization\n\nTransfer to another organization.'
    service.index_document('Guide', content)
    balance_chunks = [text for text in store.stored_texts if 'Create inventory' in text]
    assert len(balance_chunks) > 1
    assert all('## Initial balances' in text for text in balance_chunks)
    assert not any('balances.' in text and 'Transfer to another' in text for text in store.stored_texts)
    assert all(meta.get('section_path') for meta in store.stored_metadatas)


def test_large_markdown_table_repeats_headers_without_losing_rows():
    from viio_rag.application.chunks import document_chunks
    rows = [f'| Medicine{i} | {i} packages |' for i in range(30)]
    content = '# Inventory\n\n| Medicine | Quantity |\n| --- | --- |\n' + '\n'.join(rows)
    parts = list(document_chunks(content, 180, 20))
    assert len(parts) > 1
    assert all('| Medicine | Quantity |\n| --- | --- |' in text for text, _ in parts)
    assert all(any(row in text for text, _ in parts) for row in rows)
    assert all(len(text) <= 180 for text, _ in parts)


def test_fenced_heading_does_not_create_a_section():
    from viio_rag.application.chunks import document_chunks
    parts = list(document_chunks('# Real\n\n```python\n# Comment\nx = 1\n```\n\nEnd.', 300, 10))
    assert len(parts) == 1
    assert parts[0][1]['section_path'] == ['Real']
    assert '# Comment' in parts[0][0]
