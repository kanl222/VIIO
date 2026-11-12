"""Unit tests for Cross-Encoder Reranker and Redis Cache in VIIO RAG."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from langchain_core.documents import Document

from viio_rag.cache import InMemoryCache, ViioRedisCache
from viio_rag.reranker import CrossEncoderReranker
from viio_rag.retrieval import EnhancedRetrieval


@pytest.mark.asyncio
async def test_in_memory_cache_ttl_and_eviction():
    cache = InMemoryCache(max_items=3)
    cache.set("key1", "val1", ttl=60)
    cache.set("key2", "val2", ttl=60)
    cache.set("key3", "val3", ttl=60)

    assert cache.get("key1") == "val1"
    assert cache.get("key2") == "val2"
    assert cache.get("key3") == "val3"

    # Add 4th item to trigger eviction of oldest item
    cache.set("key4", "val4", ttl=60)
    assert cache.get("key4") == "val4"


@pytest.mark.asyncio
async def test_redis_cache_embedding_and_retrieval_fallback():
    # Test with offline/fallback mode (no real Redis server needed)
    cache = ViioRedisCache(redis_url="redis://invalid-host-for-test:6379/0", enabled=True)

    # Test embedding caching
    vector = [0.1, 0.2, 0.3, 0.4]
    await cache.set_embedding("тестовый запрос", "nomic-embed", vector)
    cached_vec = await cache.get_embedding("тестовый запрос", "nomic-embed")
    assert cached_vec == vector

    # Test retrieval result caching
    docs = [{"document_id": "doc1", "title": "Регламент", "text": "Правила компании"}]
    await cache.set_cached_retrieval("как взять отпуск", "role=user", 5, docs)
    cached_docs = await cache.get_cached_retrieval("как взять отпуск", "role=user", 5)
    assert cached_docs == docs


@pytest.mark.asyncio
async def test_cross_encoder_reranks_by_neural_relevance():
    # Check that reranker orders most relevant document first
    reranker = CrossEncoderReranker(model_name="BAAI/bge-reranker-v2-m3", enabled=True)

    doc_irrelevant = Document(
        page_content="Инструкция по ремонту автотранспорта и замене моторного масла.",
        metadata={"document_id": "auto", "title": "Ремонт авто"},
    )
    doc_relevant = Document(
        page_content="Порядок оформления ежегодного оплачиваемого отпуска сотрудников.",
        metadata={"document_id": "hr", "title": "Отпуска"},
    )

    query = "как оформить отпуск"
    details: list[dict] = []
    reranked = await reranker.rerank(
        query=query,
        documents=[doc_irrelevant, doc_relevant],
        k=2,
        details=details,
    )

    # Relevant HR document must be ranked #1
    assert len(reranked) == 2
    assert reranked[0].metadata["document_id"] == "hr"
    assert reranked[1].metadata["document_id"] == "auto"

    # Details must contain cross_encoder_score
    assert len(details) == 2
    assert "cross_encoder_score" in details[0]
    assert "cross_encoder_score" in details[1]
    assert details[1]["cross_encoder_score"] > details[0]["cross_encoder_score"]


@pytest.mark.asyncio
async def test_enhanced_retrieval_with_reranker_and_cache():
    allowed_hr = Document(page_content="Регламент предоставления отпуска сотрудникам", metadata={"document_id": "hr_doc", "title": "HR"})
    allowed_other = Document(page_content="Техника безопасности в складских помещениях", metadata={"document_id": "safety_doc", "title": "Склад"})

    vector = SimpleNamespace(similarity_search=lambda query, k, filter: [allowed_other, allowed_hr])
    data = SimpleNamespace(
        lexical=AsyncMock(return_value=[allowed_hr]),
        public_index=None,
        authorized_candidates=AsyncMock(return_value=[allowed_other, allowed_hr]),
    )

    reranker = CrossEncoderReranker(model_name="BAAI/bge-reranker-v2-m3", enabled=True, top_k=25)
    cache = ViioRedisCache(enabled=True)
    cache._redis_online = True

    retrieval = EnhancedRetrieval(vector, data, reranker=reranker, cache=cache)
    diagnostics: dict = {}

    # First call: executes search and populates cache
    result = await retrieval.search("правила ухода в отпуск", {"role": "employee"}, 2, diagnostics=diagnostics)
    assert len(result) == 2
    assert result[0].metadata["document_id"] == "hr_doc"
    assert diagnostics.get("reranker_model") == "BAAI/bge-reranker-v2-m3"
    assert diagnostics.get("ranking_details", {}).get("candidate_pool_size") == 25

    # Second call with identical query: must hit cache
    diag_cache: dict = {}
    cached_result = await retrieval.search("правила ухода в отпуск", {"role": "employee"}, 2, diagnostics=diag_cache)
    assert len(cached_result) == 2
    assert cached_result[0].metadata["document_id"] == "hr_doc"
    assert diag_cache.get("from_cache") is True
    assert diag_cache.get("selected", [])[0]["selection_reason"] in ("ranked", "cache_hit")


@pytest.mark.asyncio
async def test_cross_encoder_graceful_fallback_when_disabled():
    reranker = CrossEncoderReranker(enabled=False)
    doc1 = Document(page_content="распоряжение на отпуск", metadata={"document_id": "doc1", "title": "Отпуск"})
    doc2 = Document(page_content="правила работы склада", metadata={"document_id": "doc2", "title": "Склад"})

    reranked = await reranker.rerank("отпуск", [doc1, doc2], 2)
    assert len(reranked) == 1  # doc2 filtered out by lexical min_score
    assert reranked[0].metadata["document_id"] == "doc1"
