"""Sparse BM25 embedding generator for Qdrant Hybrid Search using fastembed."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from qdrant_client import models

logger = logging.getLogger(__name__)

try:
    from fastembed import SparseTextEmbedding
    FASTEMBED_SPARSE_AVAILABLE = True
except ImportError:
    SparseTextEmbedding = None
    FASTEMBED_SPARSE_AVAILABLE = False


class SparseBM25Encoder:
    """Sparse text embedding encoder generating BM25 sparse vectors for Qdrant."""

    def __init__(self, model_name: str = "Qdrant/bm25", enabled: bool = True) -> None:
        self.model_name = model_name
        self.enabled = enabled and FASTEMBED_SPARSE_AVAILABLE
        self._model: Any = None
        self._failed_init: bool = False

    def _get_model(self) -> Any:
        if not self.enabled or not FASTEMBED_SPARSE_AVAILABLE or self._failed_init:
            return None
        if self._model is None:
            try:
                self._model = SparseTextEmbedding(model_name=self.model_name)
                logger.info("Initialized Sparse BM25 encoder %s", self.model_name)
            except Exception as e:
                self._failed_init = True
                logger.warning("Failed to initialize Sparse BM25 encoder (%s). Sparse search disabled.", e)
                return None
        return self._model

    @property
    def is_available(self) -> bool:
        return self.enabled and not self._failed_init and FASTEMBED_SPARSE_AVAILABLE

    def embed_documents(self, texts: Sequence[str]) -> list[models.SparseVector]:
        """Generate SparseVector objects for a list of document texts."""
        model = self._get_model()
        if model is None or not texts:
            return [models.SparseVector(indices=[], values=[]) for _ in texts]
        try:
            embeddings = list(model.embed(texts))
            return [
                models.SparseVector(
                    indices=emb.indices.tolist() if hasattr(emb.indices, 'tolist') else list(emb.indices),
                    values=emb.values.tolist() if hasattr(emb.values, 'tolist') else list(emb.values),
                )
                for emb in embeddings
            ]
        except Exception as e:
            logger.warning("Sparse embedding generation failed: %s", e)
            return [models.SparseVector(indices=[], values=[]) for _ in texts]

    def embed_query(self, query: str) -> models.SparseVector:
        """Generate a SparseVector query object for a search query."""
        model = self._get_model()
        if model is None or not query.strip():
            return models.SparseVector(indices=[], values=[])
        try:
            embeddings = list(model.query_embed([query]))
            if not embeddings:
                return models.SparseVector(indices=[], values=[])
            emb = embeddings[0]
            return models.SparseVector(
                indices=emb.indices.tolist() if hasattr(emb.indices, 'tolist') else list(emb.indices),
                values=emb.values.tolist() if hasattr(emb.values, 'tolist') else list(emb.values),
            )
        except Exception as e:
            logger.warning("Sparse query embedding failed: %s", e)
            return models.SparseVector(indices=[], values=[])


_global_sparse_encoder: SparseBM25Encoder | None = None


def get_sparse_encoder(settings: Any = None) -> SparseBM25Encoder:
    """Return the global SparseBM25Encoder instance."""
    global _global_sparse_encoder
    if _global_sparse_encoder is None:
        enabled = getattr(settings, "sparse_bm25_enabled", getattr(settings, "hybrid_search_enabled", True))
        model_name = getattr(settings, "sparse_bm25_model", "Qdrant/bm25")
        _global_sparse_encoder = SparseBM25Encoder(model_name=model_name, enabled=enabled)
    return _global_sparse_encoder
