import pytest
from viio_rag.evaluation import (
    calculate_recall_at_k,
    calculate_precision_at_k,
    calculate_mrr,
    calculate_ndcg_at_k,
    evaluate_retrieval_batch,
)


def test_recall_at_k():
    retrieved = ["doc1", "doc2", "doc3", "doc4"]
    ground_truth = ["doc2", "doc5"]
    
    # At k=1, doc2 is not in [:1]
    assert calculate_recall_at_k(retrieved, ground_truth, k=1) == 0.0
    # At k=2, doc2 is in [:2] -> 1/2 = 0.5
    assert calculate_recall_at_k(retrieved, ground_truth, k=2) == 0.5
    # Edge cases
    assert calculate_recall_at_k([], ground_truth, k=3) == 0.0
    assert calculate_recall_at_k(retrieved, [], k=3) == 0.0


def test_precision_at_k():
    retrieved = ["doc1", "doc2", "doc3", "doc4"]
    ground_truth = ["doc1", "doc3"]

    assert calculate_precision_at_k(retrieved, ground_truth, k=2) == 0.5  # doc1 is hit, doc2 is not
    assert calculate_precision_at_k(retrieved, ground_truth, k=4) == 0.5  # doc1, doc3 hits / 4
    assert calculate_precision_at_k(retrieved, ground_truth, k=1) == 1.0  # doc1 hit / 1


def test_mrr():
    assert calculate_mrr(["doc1", "doc2", "doc3"], ["doc2"]) == 0.5
    assert calculate_mrr(["doc1", "doc2", "doc3"], ["doc1"]) == 1.0
    assert calculate_mrr(["doc1", "doc2", "doc3"], ["doc4"]) == 0.0


def test_ndcg_at_k():
    retrieved = ["doc1", "doc2", "doc3"]
    scores = {"doc1": 3.0, "doc2": 2.0, "doc3": 1.0}
    # Perfect ranking NDCG should be 1.0
    assert pytest.approx(calculate_ndcg_at_k(retrieved, scores, k=3)) == 1.0

    # Suboptimal ranking
    suboptimal = ["doc3", "doc2", "doc1"]
    ndcg = calculate_ndcg_at_k(suboptimal, scores, k=3)
    assert 0.0 < ndcg < 1.0


def test_evaluate_retrieval_batch():
    batch = [
        {"retrieved": ["d1", "d2"], "relevant": ["d1"]},
        {"retrieved": ["d3", "d4"], "relevant": ["d4"]},
    ]
    metrics = evaluate_retrieval_batch(batch, k=2)
    assert metrics["mean_recall"] == 1.0
    assert metrics["mean_precision"] == 0.5
    assert metrics["mean_mrr"] == 0.75  # (1.0 + 0.5) / 2
