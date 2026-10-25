"""Тесты для сервиса индексации документов DocumentService."""

from typing import Any
import pytest
from apiagent.services.document_service import DocumentService


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
