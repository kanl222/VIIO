"""Unit tests for Hybrid Search in Qdrant (Sparse BM25 + Dense + RRF)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from langchain_core.documents import Document

from viio_rag.sparse_bm25 import SparseBM25Encoder, get_sparse_encoder
from viio_rag.retrieval import EnhancedRetrieval, reciprocal_rank_fusion


def test_sparse_bm25_encoder_generates_vectors():
    encoder = SparseBM25Encoder(enabled=True)
    assert encoder.is_available is True

    # Test query embedding
    query_vec = encoder.embed_query("регламент отпуска приказ №1035")
    assert len(query_vec.indices) > 0
    assert len(query_vec.values) == len(query_vec.indices)

    # Test document embedding
    docs_vecs = encoder.embed_documents([
        "Каждый сотрудник оформляет приказ №1035 на отпуск.",
        "Ремонт складского оборудования и замена деталей.",
    ])
    assert len(docs_vecs) == 2
    assert len(docs_vecs[0].indices) > 0
    assert len(docs_vecs[1].indices) > 0


def test_reciprocal_rank_fusion_combines_dense_and_sparse():
    # Dense found doc_a (rank 1), doc_b (rank 2)
    doc_a = Document(page_content="Семантический текст А", metadata={"document_id": "a"})
    doc_b = Document(page_content="Семантический текст Б", metadata={"document_id": "b"})
    dense_list = [doc_a, doc_b]

    # Sparse BM25 found doc_c (rank 1), doc_a (rank 2)
    doc_c = Document(page_content="Точный артикул 1035", metadata={"document_id": "c"})
    sparse_list = [doc_c, doc_a]

    rrf = reciprocal_rank_fusion([dense_list, sparse_list], k=60)

    # doc_a appeared in both lists (rank 1 in dense, rank 2 in sparse) -> should have highest combined score
    key_a = ("a", "Семантический текст А")
    key_b = ("b", "Семантический текст Б")
    key_c = ("c", "Точный артикул 1035")

    score_a = rrf[key_a]
    score_b = rrf[key_b]
    score_c = rrf[key_c]

    # score_a = 1/61 + 1/62 ≈ 0.01639 + 0.01613 = 0.0325
    # score_c = 1/61 ≈ 0.01639
    # score_b = 1/62 ≈ 0.01613
    assert score_a > score_c
    assert score_a > score_b


@pytest.mark.asyncio
async def test_enhanced_retrieval_runs_sparse_bm25_branch_and_diagnostics():
    doc_dense = Document(page_content="Общий порядок ухода в отпуск и приказ руководителя", metadata={"document_id": "dense_1", "title": "Отпуска"})
    doc_sparse = Document(page_content="Приказ № 1035-П о графике отпусков", metadata={"document_id": "sparse_1", "title": "Приказ 1035"})

    vector_store = SimpleNamespace(
        similarity_search=lambda query, k, filter: [doc_dense],
        sparse_search=lambda query, k, filter: [doc_sparse],
    )
    data = SimpleNamespace(
        lexical=AsyncMock(return_value=[doc_dense]),
        public_index=None,
        authorized_candidates=AsyncMock(return_value=[doc_dense, doc_sparse]),
    )

    retrieval = EnhancedRetrieval(vector_store, data)
    diagnostics: dict = {}

    selected = await retrieval.search("приказ 1035", {"role": "employee"}, 2, diagnostics=diagnostics)
    assert len(selected) == 2

    # Check that sparse_bm25 branch ran and is recorded in diagnostics
    branches = diagnostics.get("branches", {})
    assert "semantic" in branches
    assert "sparse_bm25" in branches
    assert "lexical" in branches
    assert branches["sparse_bm25"]["status"] == "ok"
    assert branches["sparse_bm25"]["count"] == 1
