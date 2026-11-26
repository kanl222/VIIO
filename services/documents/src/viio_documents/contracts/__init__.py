from viio_rag.infrastructure.models.embeddings import create_embeddings

"""Пакет интерфейсов и протоколов микросервиса viio_documents."""

from viio_documents.contracts._protocols import (
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
