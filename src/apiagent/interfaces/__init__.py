"""Пакет интерфейсов и протоколов микросервиса APIAgent."""

from apiagent.interfaces._protocols import (
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
