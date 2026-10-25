"""Сервис обработки, разбиения и индексации документов."""

from __future__ import annotations

import logging
from typing import Any
import uuid
from langchain_text_splitters import RecursiveCharacterTextSplitter

from apiagent.interfaces._protocols import DocumentMetadata, IDocumentService, IVectorStore

logger = logging.getLogger(__name__)


class DocumentService(IDocumentService):
    """Сервис управления базой знаний и документами.

    Выполняет валидацию входящих текстовых данных, нарезку текста на чанки
    с использованием RecursiveCharacterTextSplitter и сохранение в векторное хранилище.

    Attributes:
        _vector_store: Векторное хранилище, реализующее протокол IVectorStore.
        _text_splitter: Сплиттер для разбиения длинных текстов на фрагменты.
    """

    def __init__(
        self,
        vector_store: IVectorStore,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ) -> None:
        """Инициализация сервиса документов.

        Args:
            vector_store: Реализация векторного хранилища (DIP).
            chunk_size: Максимальный размер текстового чанка в символах.
            chunk_overlap: Размер перекрытия между соседними чанками в символах.
        """
        self._vector_store = vector_store
        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def index_document(
        self,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> DocumentMetadata:
        """Валидация, разбиение на фрагменты и индексация документа.

        Args:
            title: Заголовок документа.
            content: Полный текст документа.
            metadata: Опциональные метаданные документа.

        Returns:
            Объект DocumentMetadata со сведениями о сохраненном документе.

        Raises:
            ValueError: Если заголовок или содержимое документа пусты.
        """
        clean_title = title.strip()
        clean_content = content.strip()

        if not clean_title:
            raise ValueError("Заголовок не может быть пустым")
        if not clean_content:
            raise ValueError("Содержимое документа не может быть пустым")

        document_id = f"doc-{uuid.uuid4().hex[:8]}"
        custom_metadata = metadata or {}

        chunks = self._text_splitter.split_text(clean_content)
        if not chunks:
            chunks = [clean_content]

        metadatas = [
            {
                **custom_metadata,
                "document_id": document_id,
                "title": clean_title,
                "chunk_index": idx,
            }
            for idx in range(len(chunks))
        ]

        logger.info(
            "Индексация документа '%s' (id: %s): создано %d чанков",
            clean_title,
            document_id,
            len(chunks),
        )

        self._vector_store.add_texts(texts=chunks, metadatas=metadatas)

        return DocumentMetadata(
            document_id=document_id,
            title=clean_title,
            chunks_count=len(chunks),
            status="indexed",
        )

    def count_documents(self) -> int:
        """Получение общего числа сохраненных текстовых чанков.

        Returns:
            Число фрагментов в хранилище.
        """
        return self._vector_store.count()
