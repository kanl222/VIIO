"""Cross-Encoder Reranker for two-stage retrieval using fastembed ONNX models."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Sequence
from functools import partial
from typing import Any
import anyio.to_thread
from langchain_core.documents import Document

logger = logging.getLogger(__name__)

try:
    from fastembed.rerank.cross_encoder import TextCrossEncoder
    FASTEMBED_AVAILABLE = True
except ImportError:
    TextCrossEncoder = None
    FASTEMBED_AVAILABLE = False


class CrossEncoderReranker:
    """Two-stage Cross-Encoder reranker using lightweight ONNX models on CPU."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        enabled: bool = True,
        top_k: int = 25,
        cache: Any = None,
    ) -> None:
        self.model_name = model_name
        self.enabled = enabled and FASTEMBED_AVAILABLE
        self.top_k = top_k
        self.cache = cache
        self._encoder: Any = None
        self._failed_init: bool = False

    def _get_encoder(self) -> Any:
        if not self.enabled or not FASTEMBED_AVAILABLE or self._failed_init:
            return None
        if self._encoder is None:
            try:
                started = time.monotonic()
                self._encoder = TextCrossEncoder(model_name=self.model_name)
                duration = round((time.monotonic() - started) * 1000)
                logger.info("Initialized Cross-Encoder %s in %d ms", self.model_name, duration)
            except Exception as e:
                self._failed_init = True
                logger.warning("Failed to initialize Cross-Encoder %s: %s. Using lexical fallback.", self.model_name, e)
                return None
        return self._encoder

    def _cache_key(self, query: str, text: str) -> str:
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        return f"viio:score:{self.model_name}:{query.strip().lower()}:{text_hash}"

    async def compute_scores(self, query: str, texts: Sequence[str]) -> list[float]:
        """Compute cross-encoder relevance logits for a query and list of document texts."""
        encoder = self._get_encoder()
        if encoder is None or not texts:
            return [0.0] * len(texts)

        scores: list[float | None] = [None] * len(texts)
        missing_indices: list[int] = []
        missing_texts: list[str] = []

        if self.cache is not None:
            for idx, text in enumerate(texts):
                cache_key = self._cache_key(query, text)
                cached_val = await self.cache.get_json(cache_key)
                if cached_val is not None:
                    scores[idx] = float(cached_val)
                else:
                    missing_indices.append(idx)
                    missing_texts.append(text)
        else:
            missing_indices = list(range(len(texts)))
            missing_texts = list(texts)

        if missing_texts:
            def _predict():
                return list(encoder.rerank(query, missing_texts))

            try:
                computed = await anyio.to_thread.run_sync(_predict)
                for idx, score in zip(missing_indices, computed):
                    scores[idx] = float(score)
                    if self.cache is not None:
                        cache_key = self._cache_key(query, texts[idx])
                        await self.cache.set_json(cache_key, float(score), ttl=86400)
            except Exception as e:
                logger.warning("Cross-encoder inference failed (%s). Falling back.", e)
                for idx in missing_indices:
                    scores[idx] = 0.0

        return [s if s is not None else 0.0 for s in scores]

    async def rerank(
        self,
        query: str,
        documents: Sequence[Document],
        k: int,
        rrf_scores: dict[tuple[Any, str], float] | None = None,
        details: list[dict] | None = None,
    ) -> list[Document]:
        """Rerank candidate documents using Cross-Encoder logits with diversity preservation.

        Falls back gracefully to lexical scoring if Cross-Encoder is disabled or unavailable.
        """
        if not documents:
            return []

        encoder = self._get_encoder()
        if encoder is None:
            # Graceful fallback to lexical ranking
            return self._lexical_rank_fallback(query, documents, k, rrf_scores=rrf_scores, details=details)

        started = time.monotonic()
        if details is not None and len(details) < len(documents):
            details.extend({} for _ in range(len(documents) - len(details)))
        texts = [doc.page_content for doc in documents]
        scores = await self.compute_scores(query, texts)
        duration_ms = round((time.monotonic() - started) * 1000)

        # Pair scores with original indices and documents
        scored = [(float(score), idx, doc) for idx, (score, doc) in enumerate(zip(scores, documents))]
        ordered = sorted(scored, key=lambda item: (-item[0], item[1]))

        # Preserve document diversity (first unique docs, then duplicates)
        first: list[tuple[int, Document]] = []
        rest: list[tuple[int, Document]] = []
        seen_docs: set[Any] = set()

        for rank, (score, index, doc) in enumerate(ordered, start=1):
            doc_id = doc.metadata.get("document_id", doc.page_content[:64])
            repeated = doc_id in seen_docs
            (rest if repeated else first).append((index, doc))
            seen_docs.add(doc_id)
            if details is not None and index < len(details):
                details[index].update(
                    cross_encoder_score=round(score, 4),
                    reranker_model=self.model_name,
                    reranker_rank=rank,
                    deferred_for_diversity=repeated,
                )

        diversified = first + rest
        if details is not None:
            for rank, (index, _) in enumerate(diversified, start=1):
                if index < len(details):
                    details[index]["diversity_rank"] = rank

        selected = [doc for _, doc in diversified[:k]]
        logger.debug("Cross-encoder reranked %d candidates to %d in %d ms", len(documents), len(selected), duration_ms)
        return selected

    def _lexical_rank_fallback(
        self,
        query: str,
        documents: Sequence[Document],
        k: int,
        rrf_scores: dict[tuple[Any, str], float] | None = None,
        details: list[dict] | None = None,
    ) -> list[Document]:
        from viio_rag.application.retrieval import rank_documents
        return rank_documents(query, documents, k, min_score=1, rrf_scores=rrf_scores, details=details)


_global_reranker: CrossEncoderReranker | None = None


def get_reranker(settings: Any = None, cache: Any = None) -> CrossEncoderReranker:
    """Return the global CrossEncoderReranker instance."""
    global _global_reranker
    if _global_reranker is None:
        model = getattr(settings, "reranker_model", "BAAI/bge-reranker-v2-m3")
        enabled = getattr(settings, "reranker_enabled", True)
        top_k = getattr(settings, "reranker_top_k", 25)
        _global_reranker = CrossEncoderReranker(model_name=model, enabled=enabled, top_k=top_k, cache=cache)
    elif cache is not None:
        _global_reranker.cache = cache
    return _global_reranker
