import pytest
from unittest.mock import MagicMock
from viio_documents.services.document_ingestion_graph import (
    build_document_ingestion_graph,
    IngestionState,
)
from viio_documents.services.document_service import DocumentService


class MockVectorStore:
    def __init__(self):
        self.stored_texts = []
        self.stored_metadatas = []
        self.added_documents = []

    def add_texts(self, texts, metadatas=None):
        self.stored_texts.extend(texts)
        if metadatas:
            self.stored_metadatas.extend(metadatas)
        return [f"id-{i}" for i in range(len(texts))]

    def add_documents(self, documents):
        self.added_documents.extend(documents)
        return [f"id-{i}" for i in range(len(documents))]

    def similarity_search(self, query, k=4):
        return []

    def delete_document(self, identifier):
        pass

    def publish_document(self, identifier):
        pass

    def count(self):
        return len(self.stored_texts)


@pytest.mark.asyncio
async def test_validate_rejects_empty_title():
    mock_store = MockVectorStore()
    doc_service = DocumentService(mock_store, chunk_size=100, chunk_overlap=10)
    graph = build_document_ingestion_graph(document_service=doc_service)

    state = {
        "title": "   ",
        "raw_data": b"Some content",
        "mime_type": "text/plain",
    }
    res = await graph.ainvoke(state)
    assert res.get("is_valid") is False
    assert "не может быть пустым" in res.get("validation_error", "")


@pytest.mark.asyncio
async def test_validate_rejects_empty_content():
    mock_store = MockVectorStore()
    doc_service = DocumentService(mock_store, chunk_size=100, chunk_overlap=10)
    graph = build_document_ingestion_graph(document_service=doc_service)

    state = {
        "title": "Правильный заголовок",
        "raw_data": b"",
        "mime_type": "text/plain",
    }
    res = await graph.ainvoke(state)
    assert res.get("is_valid") is False
    assert "не может быть пустым" in res.get("validation_error", "")


@pytest.mark.asyncio
async def test_graph_full_pipeline_text():
    mock_store = MockVectorStore()
    doc_service = DocumentService(mock_store, chunk_size=50, chunk_overlap=10)
    graph = build_document_ingestion_graph(document_service=doc_service)

    state = {
        "title": "Регламент отпусков",
        "raw_data": ("Сотрудник подает заявление за 14 календарных дней. " * 5).encode("utf-8"),
        "mime_type": "text/plain",
        "kind": "prose",
        "filename": "vacation.txt",
        "document_id": "doc-test-1",
        "allowed_roles": ["employee"],
        "allowed_departments": ["hr"],
        "is_public": True,
        "owner_id": "user-42",
    }

    res = await graph.ainvoke(state)
    assert res.get("is_valid") is True
    assert res.get("status") == "ready"
    assert res.get("chunks_count") > 1
    assert len(res.get("chunks", [])) == res.get("chunks_count")
    assert len(res.get("embedded_documents", [])) == res.get("chunks_count")
    # Verify vector store received chunks
    assert len(mock_store.stored_texts) == res.get("chunks_count")
    assert mock_store.stored_metadatas[0]["document_id"] == "doc-test-1"
    assert mock_store.stored_metadatas[0]["is_public"] is True


@pytest.mark.asyncio
async def test_graph_full_pipeline_code():
    mock_store = MockVectorStore()
    doc_service = DocumentService(mock_store, chunk_size=200, chunk_overlap=20)
    graph = build_document_ingestion_graph(document_service=doc_service)

    code_content = """def calculate_total(items: list[dict]) -> float:
    total = 0.0
    for item in items:
        total += item['price'] * item['qty']
    return total
"""
    state = {
        "title": "Calculate Function",
        "raw_data": code_content.encode("utf-8"),
        "mime_type": "text/x-python",
        "kind": "code",
        "filename": "calc.py",
        "document_id": "doc-code-1",
    }

    res = await graph.ainvoke(state)
    assert res.get("is_valid") is True
    assert res.get("status") == "ready"
    assert res.get("chunks_count") >= 1
    assert mock_store.stored_metadatas[0]["kind"] == "code"
    assert mock_store.stored_metadatas[0]["allowed_roles"] == ["programmer", "admin"]


@pytest.mark.asyncio
async def test_graph_step_retry_policy():
    # Test step retry on transient error
    attempts = 0

    class FailingStore(MockVectorStore):
        def add_texts(self, texts, metadatas=None):
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise ConnectionError("Transient network blip")
            return super().add_texts(texts, metadatas)

    failing_store = FailingStore()
    doc_service = DocumentService(failing_store, chunk_size=100, chunk_overlap=10)
    graph = build_document_ingestion_graph(document_service=doc_service)

    state = {
        "title": "Устойчивый документ",
        "raw_data": "Тестовый текст для проверки retry политики".encode("utf-8"),
        "mime_type": "text/plain",
        "document_id": "doc-retry-1",
    }

    res = await graph.ainvoke(state)
    assert res.get("status") == "ready"
    assert attempts == 2  # Proves the retry executed and succeeded on attempt 2!
