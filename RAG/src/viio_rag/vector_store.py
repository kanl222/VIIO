"""Адаптер векторного хранилища Qdrant для микросервиса viio_documents."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from viio_rag.interfaces import IVectorStore

logger = logging.getLogger(__name__)


class QdrantVectorStoreAdapter(IVectorStore):
    """Адаптер для взаимодействия с векторным хранилищем Qdrant.

    Реализует протокол `IVectorStore`, обеспечивает автоматическую инициализацию
    коллекции векторов (cold start) и абстрагирует методы индексации и семантического поиска.

    Attributes:
        _client: Клиент Qdrant для низкоуровневых операций с базой.
        _embeddings: Модель генерации эмбеддингов для текстов.
        _collection_name: Название рабочей коллекции в Qdrant.
        _store: Экземпляр QdrantVectorStore из LangChain.
    """

    def __init__(
        self,
        client: QdrantClient,
        embeddings: Embeddings,
        collection_name: str = "knowledge_base",
    ) -> None:
        """Инициализация адаптера Qdrant.

        Args:
            client: Экземпляр клиента QdrantClient.
            embeddings: Экземпляр модели Embeddings (например, OllamaEmbeddings).
            collection_name: Имя коллекции для хранения и поиска векторов.
        """
        self._client = client
        self._embeddings = embeddings
        self._collection_name = collection_name
        self._store = QdrantVectorStore(
            client=self._client,
            collection_name=self._collection_name,
            embedding=self._embeddings,
            validate_collection_config=False,
        )

    def _ensure_collection(self) -> None:
        """Проверка существования коллекции и автоматическое создание при её отсутствии."""
        try:
            if not self._client.collection_exists(self._collection_name):
                logger.info(
                    "Коллекция %s отсутствует, создание новой...", self._collection_name
                )
                sample_embedding = self._embeddings.embed_query("ping")
                vector_size = len(sample_embedding)
                try:
                    self._client.create_collection(
                        collection_name=self._collection_name,
                        vectors_config=models.VectorParams(
                            size=vector_size, distance=models.Distance.COSINE
                        ),
                    )
                    logger.info(
                        "Коллекция %s создана с размерностью векторов %d",
                        self._collection_name,
                        vector_size,
                    )
                except Exception as create_exc:
                    if "already exists" in str(create_exc).lower():
                        logger.info(
                            "Коллекция %s уже была создана параллельным процессом",
                            self._collection_name,
                        )
                    else:
                        raise
        except Exception as exc:
            logger.error("Ошибка при проверке/создании коллекции %s: %s", self._collection_name, exc)
            raise

    def add_texts(
        self, texts: list[str], metadatas: list[dict[str, Any]] | None = None
    ) -> list[str]:
        """Добавление фрагментов текста и метаданных в коллекцию Qdrant.

        Args:
            texts: Список строк для сохранения.
            metadatas: Список метаданных для каждого текстового фрагмента.

        Returns:
            Список строковых идентификаторов созданных записей.

        Raises:
            Exception: При ошибке сетевого взаимодействия с сервером Qdrant.
        """
        self._ensure_collection()
        if metadatas and all('document_id' in meta and 'chunk_index' in meta for meta in metadatas):
            ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"{meta['document_id']}:{meta['chunk_index']}")) for meta in metadatas]
            return self._store.add_texts(texts=texts, metadatas=metadatas, ids=ids)
        return self._store.add_texts(texts=texts, metadatas=metadatas)

    def similarity_search(
        self, query: str, k: int = 4, filter: Any | None = None
    ) -> list[Document]:
        """Поиск наиболее релевантных фрагментов по запросу.

        Args:
            query: Текстовый поисковый запрос.
            k: Количество возвращаемых результатов.
            filter: Опциональный фильтр метаданных (qdrant models.Filter).

        Returns:
            Список объектов Document с текстом и метаданными. Пустой список, если коллекция еще не создана.
        """
        try:
            if k <= 0:
                return []
            ready = models.Filter(must_not=[models.FieldCondition(key=key, match=models.MatchValue(value=False))
                for key in ('metadata.ingestion_ready', 'metadata.is_active')])
            filter = models.Filter(must=[filter, ready]) if filter is not None else ready
            semantic = self._store.similarity_search(query=query, k=k, filter=filter)
            titles = self._find_acronym_titles(query, k, filter)
            if not titles:
                return semantic
            # Exact system names are often lost by embeddings on short acronym questions.
            combined = []
            seen = set()
            for doc in titles[:max(1, k // 2)] + semantic + titles:
                identity = (doc.metadata.get("document_id"), doc.metadata.get("chunk_index"), doc.page_content)
                if identity not in seen:
                    seen.add(identity)
                    combined.append(doc)
                if len(combined) == k:
                    break
            return combined
        except Exception as exc:
            # Query directly: a missing collection is the cold-start case.
            # Do not cache existence or swallow network/authentication failures.
            if isinstance(exc, UnexpectedResponse) and exc.status_code == 404:
                return []
            if isinstance(exc, ValueError) and str(exc) == f"Collection {self._collection_name} not found":
                return []
            logger.error("Ошибка выполнения similarity_search в Qdrant: %s", exc)
            raise

    def _find_acronym_titles(self, query: str, k: int, access_filter: Any | None) -> list[Document]:
        terms = re.findall(r"\b[A-ZА-ЯЁ][A-ZА-ЯЁ0-9]{2,11}\b", query)
        if not terms and re.fullmatch(r"[a-zа-яё]{3,12}", query.strip()):
            terms = [query.strip().upper()]
        if not terms or k <= 0:
            return []
        variants = set(terms)
        for term in terms:
            # ИСЛО and ИС ЛО / ИС «ЛО» should find the same title; likewise other initialisms.
            variants.update(f"{term[:index]} {term[index:]}" for index in range(2, len(term) - 1))
        title_filter = models.Filter(should=[models.FieldCondition(
            key="metadata.title", match=models.MatchText(text=value)
        ) for value in sorted(variants)])
        combined_filter = models.Filter(must=[access_filter, title_filter]) if access_filter is not None else title_filter
        points, _ = self._client.scroll(
            collection_name=self._collection_name, scroll_filter=combined_filter,
            limit=64, with_payload=True, with_vectors=False,
        )
        normalized = {term.casefold() for term in terms}
        documents = [Document(page_content=point.payload.get("page_content", ""), metadata=point.payload.get("metadata", {}))
                     for point in points if point.payload and point.payload.get("page_content")]
        documents.sort(key=lambda doc: (
            re.sub(r"[^\w]", "", str(doc.metadata.get("title", ""))).casefold() not in normalized,
            doc.metadata.get("chunk_index", 0), str(doc.metadata.get("title", "")),
        ))
        # Give distinct source documents a chance before adding subsequent chunks.
        first, remaining, seen = [], [], set()
        for doc in documents:
            identity = doc.metadata.get("document_id", doc.metadata.get("title"))
            if identity in seen:
                remaining.append(doc)
            else:
                seen.add(identity)
                first.append(doc)
        return (first + remaining)[:k]

    def delete_document(self, identifier: str) -> None:
        if self._client.collection_exists(self._collection_name):
            self._client.delete(self._collection_name, models.FilterSelector(filter=models.Filter(must=[
                models.FieldCondition(key='metadata.document_id', match=models.MatchValue(value=identifier)),
            ])), wait=True)

    def publish_document(self, identifier: str) -> None:
        self._client.set_payload(self._collection_name, payload={'ingestion_ready': True},
            points=models.Filter(must=[models.FieldCondition(key='metadata.document_id', match=models.MatchValue(value=identifier))]),
            key='metadata', wait=True)

    def delete_document_tail(self, identifier: str, chunks_count: int) -> None:
        """Remove obsolete chunk indices only after the replacement was written."""
        self._client.delete(self._collection_name, models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key='metadata.document_id', match=models.MatchValue(value=identifier)),
            models.FieldCondition(key='metadata.chunk_index', range=models.Range(gte=chunks_count)),
        ])), wait=True)

    def set_document_active(self, identifier, active):
        self._client.set_payload(self._collection_name, payload={'is_active': active},
            points=models.Filter(must=[models.FieldCondition(key='metadata.document_id', match=models.MatchValue(value=identifier))]),
            key='metadata', wait=True)

    def document_metadata(self):
        if not self._client.collection_exists(self._collection_name):
            return []
        found = {}
        offset = None
        while True:
            points, offset = self._client.scroll(self._collection_name, offset=offset, limit=100, with_vectors=False)
            for point in points:
                meta = (point.payload or {}).get('metadata', {})
                identifier = meta.get('document_id')
                if identifier and meta.get('ingestion_ready') is not False:
                    record = found.setdefault(identifier, {**meta, 'chunks_count': 0, '_search_parts': []})
                    record['chunks_count'] += 1
                    record.setdefault('_search_parts', []).append((meta.get('chunk_index', 0), (point.payload or {}).get('page_content', '')))
            if offset is None:
                for record in found.values():
                    record['search_text'] = '\n\n'.join(text for _, text in sorted(record.pop('_search_parts')))
                return list(found.values())

    def count(self) -> int:
        """Получение общего числа точек в коллекции.

        Returns:
            Количество документов (точек) в коллекции Qdrant. Возвращает 0, если коллекция отсутствует.
        """
        try:
            if not self._client.collection_exists(self._collection_name):
                return 0
            return self._client.count(collection_name=self._collection_name).count
        except Exception as exc:
            logger.error("Ошибка при получении количества документов: %s", exc)
            raise
