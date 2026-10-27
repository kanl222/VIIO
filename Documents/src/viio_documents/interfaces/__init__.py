"""Пакет интерфейсов и протоколов микросервиса viio_documents."""

from viio_documents.interfaces._protocols import (
    DocumentMetadata,
    IAgentService,
    IDocumentService,
    IVectorStore,
)

__all__ = [
    "DocumentMetadata",
    "IAgentService",
    "IDocumentService",
    "IVectorStore",
]
