"""Сервис обработки, разбиения и индексации документов."""

from __future__ import annotations

import logging
import re
from typing import Literal
from typing import Any
import uuid
from langchain_text_splitters import RecursiveCharacterTextSplitter

from viio_documents.interfaces._protocols import DocumentMetadata, IDocumentService, IVectorStore
from viio_documents.services.code_documents import split_code

logger = logging.getLogger(__name__)


def split_document_text(
    content: str, chunk_size: int = 500, chunk_overlap: int = 50,
    chunk_unit: Literal["characters", "words"] = "characters",
) -> list[str]:
    """Split by characters or word spans, preserving source line breaks."""
    if chunk_size <= 0 or not 0 <= chunk_overlap < chunk_size:
        raise ValueError("Размер фрагмента должен быть положительным, перекрытие — меньше размера")
    if chunk_unit == "characters":
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        ).split_text(content)
    if chunk_unit != "words":
        raise ValueError("Неизвестная единица разбиения текста")
    words = list(re.finditer(r"\S+", content))
    chunks = []
    for start in range(0, len(words), chunk_size - chunk_overlap):
        end = min(start + chunk_size, len(words))
        chunks.append(content[words[start].start():words[end - 1].end()])
        if end == len(words):
            break
    return chunks


class DocumentService(IDocumentService):
    """Сервис управления базой знаний и документами.

    Выполняет валидацию входящих текстовых данных, нарезку текста на чанки
    с использованием RecursiveCharacterTextSplitter и сохранение в векторное хранилище.
    Регистрацию в каталоге выполняет асинхронный сервис загрузки.

    Attributes:
        _vector_store: Векторное хранилище, реализующее протокол IVectorStore.
        _chunk_unit: Единицы размера фрагментов: символы или слова.
    """

    def __init__(
        self,
        vector_store: IVectorStore,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        chunk_unit: Literal["characters", "words"] = "characters",
    ) -> None:
        """Инициализация сервиса документов.

        Args:
            vector_store: Реализация векторного хранилища (DIP).
            chunk_size: Максимальный размер фрагмента в выбранных единицах.
            chunk_overlap: Перекрытие в тех же единицах.
            chunk_unit: Символы или слова.
        """
        self._vector_store = vector_store
        split_document_text("", chunk_size, chunk_overlap, chunk_unit)
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._chunk_unit = chunk_unit

    def index_document(
        self,
        title: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        is_public: bool = False,
        allowed_roles: list[str] | None = None,
        allowed_departments: list[str] | None = None,
        owner_id: str | None = None,
        *,
        document_id: str | None = None,
        mime_type: str = "text/plain",
        sha256: str | None = None,
        storage_key: str | None = None,
    ) -> DocumentMetadata:
        """Валидация, разбиение на фрагменты и индексация документа с атрибутами доступа.

        Args:
            title: Заголовок документа.
            content: Полный текст документа.
            metadata: Опциональные метаданные документа.
            is_public: Доступен ли документ всем пользователям.
            allowed_roles: Список разрешенных ролей (например, ["admin", "employee"]).
            allowed_departments: Список разрешенных подразделений (например, ["it", "sales"]).
            owner_id: Идентификатор владельца документа.
            document_id: Использовать указанный id вместо генерации нового
                (для пакетной загрузки, где id выделяется заранее).
            mime_type: MIME-тип документа (передаётся в каталог).
            sha256: SHA-256 хеш оригинала (передаётся в каталог).
            storage_key: Ключ файла в хранилище оригиналов (передаётся в каталог).

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

        doc_id = document_id or f"doc-{uuid.uuid4().hex[:8]}"
        custom_metadata = metadata or {}

        code_parts = split_code(content, str(custom_metadata.get('filename', title))) if custom_metadata.get('kind') == 'code' else None
        chunks = [part[0] for part in code_parts] if code_parts else split_document_text(clean_content, self._chunk_size, self._chunk_overlap, self._chunk_unit)
        if not chunks:
            chunks = [clean_content]

        roles = allowed_roles if allowed_roles is not None else ["employee"]
        depts = allowed_departments if allowed_departments is not None else ["general"]

        metadatas = [
            {
                **custom_metadata,
                **({'line_start': code_parts[idx][1], 'line_end': code_parts[idx][2]} if code_parts else {}),
                "document_id": doc_id,
                "title": clean_title,
                "chunk_index": idx,
                "chunk_unit": self._chunk_unit,
                "chunk_size": self._chunk_size,
                "chunk_overlap": self._chunk_overlap,
                "is_public": is_public,
                "allowed_roles": roles,
                "allowed_departments": depts,
                "owner_id": owner_id,
            }
            for idx in range(len(chunks))
        ]

        logger.info(
            "Индексация документа '%s' (id: %s): создано %d чанков",
            clean_title,
            doc_id,
            len(chunks),
        )

        self._vector_store.add_texts(texts=chunks, metadatas=metadatas)

        return DocumentMetadata(
            document_id=doc_id,
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

    def delete_document(self, identifier):
        self._vector_store.delete_document(identifier)

    def publish_document(self, identifier):
        self._vector_store.publish_document(identifier)

    def set_document_active(self, identifier, active):
        self._vector_store.set_document_active(identifier, active)

    def existing_document_metadata(self):
        return self._vector_store.document_metadata()
