"""Тесты для адаптера векторного хранилища QdrantVectorStoreAdapter."""

from langchain_core.embeddings import FakeEmbeddings
from qdrant_client import QdrantClient
from viio_rag.infrastructure.vector_store import QdrantVectorStoreAdapter
from viio_rag.application.search_tool import build_access_filter
from langchain_core.documents import Document


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


def test_qdrant_vector_store_race_condition_already_exists(monkeypatch) -> None:
    """Проверка корректной обработки ситуации, когда коллекция уже создана параллельным потоком."""
    client = QdrantClient(":memory:")
    embeddings = FakeEmbeddings(size=8)
    adapter = QdrantVectorStoreAdapter(
        client=client,
        embeddings=embeddings,
        collection_name="race_collection",
    )

    # Симулируем, что collection_exists сначала вернул False, но create_collection выбросил ошибку 'already exists',
    # после чего параллельный процесс уже создал коллекцию (collection_exists -> True)
    exists_calls = [False, True]

    def fake_collection_exists(*args, **kwargs):
        return exists_calls.pop(0) if exists_calls else True

    def fake_create_collection(*args, **kwargs):
        raise ValueError("Collection race_collection already exists")

    monkeypatch.setattr(client, "collection_exists", fake_collection_exists)
    monkeypatch.setattr(client, "create_collection", fake_create_collection)

    # Метод не должен выбрасывать исключение
    adapter._ensure_collection()


def test_qdrant_vector_store_add_texts_retries_on_404(monkeypatch) -> None:
    """Проверка повторной инициализации коллекции при 404 во время upsert."""
    client = QdrantClient(":memory:")
    embeddings = FakeEmbeddings(size=8)
    adapter = QdrantVectorStoreAdapter(
        client=client,
        embeddings=embeddings,
        collection_name="retry_404_collection",
    )

    from qdrant_client.http.exceptions import UnexpectedResponse
    import httpx

    original_add = adapter._store.add_texts
    first_call = True

    def flakily_add(*args, **kwargs):
        nonlocal first_call
        if first_call:
            first_call = False
            response = httpx.Response(404, request=httpx.Request("POST", "http://qdrant:6333/collections/retry_404_collection/points"))
            raise UnexpectedResponse.for_response(response)
        return original_add(*args, **kwargs)

    monkeypatch.setattr(adapter._store, "add_texts", flakily_add)

    # При первом вызове будет 404, адаптер должен вызвать _ensure_collection(force=True) и успешно повторить
    ids = adapter.add_texts(["тестовый текст"])
    assert len(ids) == 1
    assert not first_call


def test_acronym_title_search_boosts_exact_source_without_bypassing_acl(monkeypatch) -> None:
    client = QdrantClient(":memory:")
    adapter = QdrantVectorStoreAdapter(client, FakeEmbeddings(size=8), "acronym_collection")
    adapter.add_texts(
        ["Описание системы", "Секретный документ", "Общий регламент"],
        [
            {"title": "ИСЛО", "document_id": "public-system", "chunk_index": 0,
             "is_public": False, "allowed_roles": ["employee"], "allowed_departments": []},
            {"title": "ИСЛО", "document_id": "secret-system", "chunk_index": 0,
             "is_public": False, "allowed_roles": ["admin"], "allowed_departments": []},
            {"title": "Регламент", "document_id": "general", "chunk_index": 0,
             "is_public": True, "allowed_roles": [], "allowed_departments": []},
        ],
    )
    # Воспроизводим поведение эмбеддингов, когда для короткой аббревиатуры первым ранжируется общий результат.
    generic = Document(page_content="Общий регламент", metadata={"document_id": "general", "chunk_index": 0})
    monkeypatch.setattr(adapter._store, "similarity_search", lambda **kwargs: [generic])
    docs = adapter.similarity_search("Что такое ИСЛО?", k=3, filter=build_access_filter({"role": "employee", "departments": []}))
    assert docs[0].metadata["document_id"] == "public-system"
    assert all(doc.metadata["document_id"] != "secret-system" for doc in docs)
    assert len({doc.metadata["document_id"] for doc in docs}) == len(docs)
