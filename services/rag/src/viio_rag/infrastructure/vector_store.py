"""Адаптер векторного хранилища Qdrant для микросервиса viio_documents."""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, Literal
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from viio_rag.contracts.interfaces import IVectorStore
from viio_rag.infrastructure.models.sparse_bm25 import get_sparse_encoder

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

    def _ensure_collection(self, force: bool = False) -> None:
        """Проверка существования коллекции и автоматическое создание при её отсутствии."""
        try:
            if force or not self._client.collection_exists(self._collection_name):
                logger.info(
                    "Коллекция %s отсутствует, создание новой...", self._collection_name
                )
                try:
                    sample_embedding = self._embeddings.embed_query("ping")
                    vector_size = len(sample_embedding)
                except Exception as embed_exc:
                    logger.warning(
                        "Не удалось получить размерность эмбеддингов для %s (%s), используется значение по умолчанию 768",
                        self._collection_name,
                        embed_exc,
                    )
                    vector_size = 768

                try:
                    self._client.create_collection(
                        collection_name=self._collection_name,
                        vectors_config=models.VectorParams(
                            size=vector_size, distance=models.Distance.COSINE
                        ),
                        sparse_vectors_config={
                            "bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)
                        },
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

                # Убеждаемся, что коллекция действительно доступна (в т.ч. при параллельном создании)
                for _ in range(10):
                    if self._client.collection_exists(self._collection_name):
                        break
                    time.sleep(0.5)
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
        else:
            ids = None

        def _do_add() -> list[str]:
            if ids:
                return self._store.add_texts(texts=texts, metadatas=metadatas, ids=ids)
            return self._store.add_texts(texts=texts, metadatas=metadatas)

        try:
            res_ids = _do_add()
        except UnexpectedResponse as exc:
            if exc.status_code == 404:
                logger.warning(
                    "Коллекция %s не найдена в Qdrant (404) при upsert. Повторная инициализация...",
                    self._collection_name,
                )
                self._ensure_collection(force=True)
                res_ids = _do_add()
            else:
                raise

        try:
            encoder = get_sparse_encoder()
            if encoder.is_available and res_ids:
                sparse_vectors = encoder.embed_documents(texts)
                point_vectors = [
                    models.PointVectors(
                        id=pid,
                        vector={"bm25": svec}
                    )
                    for pid, svec in zip(res_ids, sparse_vectors)
                    if svec.indices
                ]
                if point_vectors:
                    self._client.update_vectors(
                        collection_name=self._collection_name,
                        points=point_vectors,
                        wait=False,
                    )
        except Exception as e:
            logger.debug("Не удалось сохранить sparse векторы: %s", e)

        return res_ids

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
            # Точные названия систем часто теряются эмбеддингами в коротких вопросах с аббревиатурами.
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
            # Запрашиваем напрямую: отсутствие коллекции — случай холодного старта.
            # Не кэшируем факт существования и не подавляем ошибки сети/аутентификации.
            if isinstance(exc, UnexpectedResponse) and exc.status_code == 404:
                return []
            if isinstance(exc, ValueError) and str(exc) == f"Collection {self._collection_name} not found":
                return []
            logger.error("Ошибка выполнения similarity_search в Qdrant: %s", exc)
            raise

    def sparse_search(
        self, query: str, k: int = 4, filter: Any | None = None
    ) -> list[Document]:
        """Поиск наиболее релевантных фрагментов по Sparse BM25 вектору.

        Args:
            query: Текстовый поисковый запрос.
            k: Количество кандидатов.
            filter: Фильтр прав доступа.

        Returns:
            Список найденных документов Document.
        """
        if k <= 0 or not query.strip():
            return []
        try:
            encoder = get_sparse_encoder()
            if not encoder.is_available:
                return []

            sparse_vec = encoder.embed_query(query)
            if not sparse_vec.indices:
                return []

            ready = models.Filter(must_not=[models.FieldCondition(key=key, match=models.MatchValue(value=False))
                for key in ('metadata.ingestion_ready', 'metadata.is_active')])
            combined_filter = models.Filter(must=[filter, ready]) if filter is not None else ready

            try:
                response = self._client.query_points(
                    collection_name=self._collection_name,
                    query=sparse_vec,
                    using="bm25",
                    query_filter=combined_filter,
                    limit=k,
                    with_payload=True,
                )
                points = response.points if hasattr(response, 'points') else []
                if points:
                    return [
                        Document(
                            page_content=p.payload.get("page_content", ""),
                            metadata=p.payload.get("metadata", {})
                        )
                        for p in points if p.payload and p.payload.get("page_content")
                    ]
            except Exception as e:
                logger.debug("Qdrant sparse query_points using 'bm25' unavailable (%s), using payload scroll fallback", e)

            return self._sparse_payload_search(query, k, combined_filter)
        except Exception as exc:
            if isinstance(exc, UnexpectedResponse) and exc.status_code == 404:
                return []
            logger.debug("Sparse search unavailable: %s", exc)
            return []

    def hybrid_search(
        self,
        query: str,
        k: int = 4,
        filter: Any | None = None,
        fusion: Literal["rrf", "dbsf"] = "rrf",
        prefetch_limit: int = 40,
    ) -> list[Document]:
        """Серверный гибридный поиск Qdrant через Query API (Dense + Sparse BM25 + RRF/DBSF).

        Args:
            query: Текстовый поисковый запрос.
            k: Количество возвращаемых результатов.
            filter: Опциональный фильтр метаданных (qdrant models.Filter).
            fusion: Метод слияния на стороне Qdrant ('rrf' или 'dbsf').
            prefetch_limit: Глубина выборки кандидатов для каждой параллельной ветки prefetch.

        Returns:
            Список найденных документов Document с метаданными.
        """
        if k <= 0 or not query.strip():
            return []

        ready = models.Filter(
            must_not=[
                models.FieldCondition(key=key, match=models.MatchValue(value=False))
                for key in ('metadata.ingestion_ready', 'metadata.is_active')
            ]
        )
        combined_filter = models.Filter(must=[filter, ready]) if filter is not None else ready

        try:
            encoder = get_sparse_encoder()
            sparse_vec = encoder.embed_query(query) if encoder.is_available else None

            # Если разреженный энкодер недоступен или не вернул токенов — откат на similarity_search
            if sparse_vec is None or not sparse_vec.indices:
                return self.similarity_search(query=query, k=k, filter=filter)

            dense_vec = self._embeddings.embed_query(query)
            prefetch_candidates = max(prefetch_limit, k * 2, 10)

            dense_prefetch = models.Prefetch(
                query=dense_vec,
                using="",
                limit=prefetch_candidates,
                filter=combined_filter,
            )
            sparse_prefetch = models.Prefetch(
                query=sparse_vec,
                using="bm25",
                limit=prefetch_candidates,
                filter=combined_filter,
            )
            fusion_type = models.Fusion.DBSF if str(fusion).lower() == "dbsf" else models.Fusion.RRF
            fusion_query = models.FusionQuery(fusion=fusion_type)

            response = self._client.query_points(
                collection_name=self._collection_name,
                prefetch=[dense_prefetch, sparse_prefetch],
                query=fusion_query,
                limit=k,
                with_payload=True,
            )
            points = response.points if hasattr(response, 'points') else []
            results = [
                Document(
                    page_content=p.payload.get("page_content", ""),
                    metadata=p.payload.get("metadata", {}),
                )
                for p in points
                if p.payload and p.payload.get("page_content")
            ]

            # Точные названия систем часто теряются эмбеддингами в коротких вопросах с аббревиатурами
            titles = self._find_acronym_titles(query, k, combined_filter)
            if not titles:
                return results

            combined = []
            seen = set()
            for doc in titles[:max(1, k // 2)] + results + titles:
                identity = (doc.metadata.get("document_id"), doc.metadata.get("chunk_index"), doc.page_content)
                if identity not in seen:
                    seen.add(identity)
                    combined.append(doc)
                if len(combined) == k:
                    break
            return combined

        except Exception as exc:
            if isinstance(exc, UnexpectedResponse) and exc.status_code == 404:
                return []
            if isinstance(exc, ValueError) and str(exc) == f"Collection {self._collection_name} not found":
                return []
            logger.warning("Ошибка Query API в hybrid_search (%s), откат на similarity_search", exc)
            return self.similarity_search(query=query, k=k, filter=filter)

    def _sparse_payload_search(self, query: str, k: int, combined_filter: Any | None) -> list[Document]:
        words = [w for w in re.findall(r'\b\w{3,}\b', query) if not w.isdigit()]
        if not words:
            return []
        text_filter = models.Filter(should=[
            models.FieldCondition(key="page_content", match=models.MatchText(text=w))
            for w in words[:6]
        ])
        q_filter = models.Filter(must=[combined_filter, text_filter]) if combined_filter else text_filter
        try:
            points, _ = self._client.scroll(
                collection_name=self._collection_name,
                scroll_filter=q_filter,
                limit=k,
                with_payload=True,
                with_vectors=False,
            )
            return [
                Document(page_content=p.payload.get("page_content", ""), metadata=p.payload.get("metadata", {}))
                for p in points if p.payload and p.payload.get("page_content")
            ]
        except Exception:
            return []

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
        # Даем шанс отдельным исходным документам перед добавлением последующих чанков.
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
            try:
                self._client.delete(self._collection_name, models.FilterSelector(filter=models.Filter(must=[
                    models.FieldCondition(key='metadata.document_id', match=models.MatchValue(value=identifier)),
                ])), wait=True)
            except UnexpectedResponse as exc:
                if exc.status_code != 404:
                    raise

    def publish_document(self, identifier: str) -> None:
        if not self._client.collection_exists(self._collection_name):
            return
        try:
            self._client.set_payload(self._collection_name, payload={'ingestion_ready': True},
                points=models.Filter(must=[models.FieldCondition(key='metadata.document_id', match=models.MatchValue(value=identifier))]),
                key='metadata', wait=True)
        except UnexpectedResponse as exc:
            if exc.status_code != 404:
                raise

    def delete_document_tail(self, identifier: str, chunks_count: int) -> None:
        """Remove obsolete chunk indices only after the replacement was written."""
        if not self._client.collection_exists(self._collection_name):
            return
        try:
            self._client.delete(self._collection_name, models.FilterSelector(filter=models.Filter(must=[
                models.FieldCondition(key='metadata.document_id', match=models.MatchValue(value=identifier)),
                models.FieldCondition(key='metadata.chunk_index', range=models.Range(gte=chunks_count)),
            ])), wait=True)
        except UnexpectedResponse as exc:
            if exc.status_code != 404:
                raise

    def set_document_active(self, identifier, active):
        if not self._client.collection_exists(self._collection_name):
            return
        try:
            self._client.set_payload(self._collection_name, payload={'is_active': active},
                points=models.Filter(must=[models.FieldCondition(key='metadata.document_id', match=models.MatchValue(value=identifier))]),
                key='metadata', wait=True)
        except UnexpectedResponse as exc:
            if exc.status_code != 404:
                raise

    def get_chunks(self, doc_chunks: list[tuple[str, int]]) -> list[Document]:
        """Прямое извлечение чанков по парам (document_id, chunk_index) через детерминированные ID."""
        if not doc_chunks or not self._client.collection_exists(self._collection_name):
            return []
        point_ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{chunk_idx}"))
                     for doc_id, chunk_idx in doc_chunks]
        try:
            points = self._client.retrieve(
                collection_name=self._collection_name,
                ids=point_ids,
                with_payload=True,
                with_vectors=False,
            )
            ready_docs = []
            for point in points:
                if not point.payload or not point.payload.get("page_content"):
                    continue
                meta = point.payload.get("metadata", {})
                if meta.get("ingestion_ready") is False or meta.get("is_active") is False:
                    continue
                ready_docs.append(Document(page_content=point.payload["page_content"], metadata=meta))
            return ready_docs
        except Exception as exc:
            logger.warning("Ошибка при получении соседних чанков: %s", exc)
            return []

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
