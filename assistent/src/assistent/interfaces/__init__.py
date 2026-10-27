"""Пакет интерфейсов и протоколов микросервиса assistent."""

from assistent.interfaces._protocols import (
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
