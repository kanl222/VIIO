"""Тесты для адаптера векторного хранилища QdrantVectorStoreAdapter."""

from langchain_core.embeddings import FakeEmbeddings
from qdrant_client import QdrantClient
from apiagent.utils.vector_store import QdrantVectorStoreAdapter


def test_qdrant_vector_store_adapter_in_memory() -> None:
    """Проверка работы QdrantVectorStoreAdapter с in-memory QdrantClient."""
    client = QdrantClient(":memory:")
    embeddings = FakeEmbeddings(size=8)
    adapter = QdrantVectorStoreAdapter(
        client=client,
        embeddings=embeddings,
        collection_name="test_collection",
    )

    # До индексации коллекция еще не создана
    assert adapter.count() == 0
    assert adapter.similarity_search("любой запрос") == []

    # Добавление текстов
    texts = ["Документ 1 про возврат", "Документ 2 про доставку"]
    metadatas = [{"title": "Возврат"}, {"title": "Доставка"}]
    ids = adapter.add_texts(texts=texts, metadatas=metadatas)

    assert len(ids) == 2
    assert adapter.count() == 2

    # Поиск
    results = adapter.similarity_search("возврат", k=2)
    assert len(results) == 2
    assert any("возврат" in doc.page_content.lower() for doc in results)
