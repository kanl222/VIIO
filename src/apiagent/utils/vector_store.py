"""Адаптер векторного хранилища Qdrant для микросервиса APIAgent."""

from __future__ import annotations

import logging
from typing import Any
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models

from apiagent.interfaces._protocols import IVectorStore

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
        return self._store.add_texts(texts=texts, metadatas=metadatas)

    def similarity_search(self, query: str, k: int = 4) -> list[Document]:
        """Поиск наиболее релевантных фрагментов по запросу.

        Args:
            query: Текстовый поисковый запрос.
            k: Количество возвращаемых результатов.

        Returns:
            Список объектов Document с текстом и метаданными. Пустой список, если коллекция еще не создана.
        """
        try:
            if not self._client.collection_exists(self._collection_name):
                return []
            return self._store.similarity_search(query=query, k=k)
        except Exception as exc:
            logger.error("Ошибка выполнения similarity_search в Qdrant: %s", exc)
            raise

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
