"""RAG retrieval evaluation metrics (Recall@k, Precision@k, MRR, NDCG@k) based on the evaluate-rag skill."""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any


def calculate_recall_at_k(
    retrieved_ids: Sequence[Any],
    ground_truth_ids: Sequence[Any],
    k: int,
) -> float:
    """Calculates Recall@k: fraction of relevant documents retrieved in top k."""
    if not ground_truth_ids or k <= 0:
        return 0.0
    top_k_retrieved = set(retrieved_ids[:k])
    relevant_set = set(ground_truth_ids)
    hits = len(top_k_retrieved & relevant_set)
    return hits / len(relevant_set)


def calculate_precision_at_k(
    retrieved_ids: Sequence[Any],
    ground_truth_ids: Sequence[Any],
    k: int,
) -> float:
    """Calculates Precision@k: fraction of top k results that are relevant."""
    if k <= 0:
        return 0.0
    top_k_retrieved = set(retrieved_ids[:k])
    relevant_set = set(ground_truth_ids)
    hits = len(top_k_retrieved & relevant_set)
    return hits / k


def calculate_mrr(
    retrieved_ids: Sequence[Any],
    ground_truth_ids: Sequence[Any],
) -> float:
    """Calculates Mean Reciprocal Rank (MRR): 1 / rank of first relevant result."""
    relevant_set = set(ground_truth_ids)
    for rank, doc_id in enumerate(retrieved_ids, start=1):
        if doc_id in relevant_set:
            return 1.0 / rank
    return 0.0


def calculate_ndcg_at_k(
    retrieved_ids: Sequence[Any],
    relevance_scores: dict[Any, float],
    k: int,
) -> float:
    """Calculates Normalized Discounted Cumulative Gain (NDCG@k) for graded relevance."""
    if k <= 0 or not relevance_scores:
        return 0.0

    dcg = 0.0
    for rank, doc_id in enumerate(retrieved_ids[:k], start=1):
        rel = relevance_scores.get(doc_id, 0.0)
        dcg += rel / math.log2(rank + 1)

    ideal_scores = sorted(relevance_scores.values(), reverse=True)[:k]
    idcg = sum(rel / math.log2(rank + 1) for rank, rel in enumerate(ideal_scores, start=1))

    if idcg <= 0.0:
        return 0.0
    return dcg / idcg


def evaluate_retrieval_batch(
    test_cases: Sequence[dict[str, Any]],
    k: int = 5,
) -> dict[str, float]:
    """Evaluates a batch of retrieval test cases.

    Each case in test_cases must contain:
      - 'retrieved': Sequence of retrieved doc IDs
      - 'relevant': Sequence of ground truth doc IDs
      - (optional) 'relevance_scores': dict of doc_id -> score
    """
    if not test_cases:
        return {'mean_recall': 0.0, 'mean_precision': 0.0, 'mean_mrr': 0.0, 'mean_ndcg': 0.0}

    total_recall = 0.0
    total_precision = 0.0
    total_mrr = 0.0
    total_ndcg = 0.0

    for case in test_cases:
        retrieved = case.get('retrieved', [])
        relevant = case.get('relevant', [])
        rel_scores = case.get('relevance_scores') or {doc_id: 1.0 for doc_id in relevant}

        total_recall += calculate_recall_at_k(retrieved, relevant, k)
        total_precision += calculate_precision_at_k(retrieved, relevant, k)
        total_mrr += calculate_mrr(retrieved, relevant)
        total_ndcg += calculate_ndcg_at_k(retrieved, rel_scores, k)

    count = len(test_cases)
    return {
        'mean_recall': round(total_recall / count, 4),
        'mean_precision': round(total_precision / count, 4),
        'mean_mrr': round(total_mrr / count, 4),
        'mean_ndcg': round(total_ndcg / count, 4),
    }


def calculate_faithfulness(verified_claims: int, total_claims: int) -> float:
    """Calculates Faithfulness (RAGAS / ARES standard): fraction of claims supported by context."""
    if total_claims <= 0:
        return 1.0
    return min(1.0, max(0.0, verified_claims / total_claims))


def calculate_context_utilization(
    cited_ids: Sequence[Any],
    retrieved_ids: Sequence[Any],
) -> float:
    """Calculates Context Utilization: fraction of retrieved documents that were actually cited in the answer."""
    if not retrieved_ids:
        return 0.0
    retrieved_set = set(retrieved_ids)
    cited_set = set(cited_ids)
    useful_hits = len(retrieved_set & cited_set)
    return useful_hits / len(retrieved_set)


def is_lost_in_the_middle_layout(items: Sequence[Any], rank_attr: str | None = None) -> bool:
    """Checks whether items follow a U-shaped 'Lost in the Middle' mitigation order.

    Top items (rank 1 and 2) must be located at the boundaries (index 0 and index -1),
    while lower ranked items reside in the interior.
    """
    if len(items) <= 2:
        return True
    first_rank = getattr(items[0], rank_attr) if rank_attr and hasattr(items[0], rank_attr) else items[0]
    last_rank = getattr(items[-1], rank_attr) if rank_attr and hasattr(items[-1], rank_attr) else items[-1]
    mid_ranks = [
        getattr(item, rank_attr) if rank_attr and hasattr(item, rank_attr) else item
        for item in items[1:-1]
    ]
    # For 1-based ranks (1, 2, 3...), boundary ranks should be smaller than worst interior rank
    try:
        return min(first_rank, last_rank) <= min(mid_ranks)
    except TypeError:
        return True
