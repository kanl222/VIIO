import pytest
from viio_rag.evaluation import (
    calculate_recall_at_k,
    calculate_precision_at_k,
    calculate_mrr,
    calculate_ndcg_at_k,
    evaluate_retrieval_batch,
    calculate_faithfulness,
    calculate_context_utilization,
    is_lost_in_the_middle_layout,
    build_ragas_dataset,
    evaluate_with_ragas,
)
from viio_rag.retrieval import reorder_lost_in_the_middle


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


def test_faithfulness_and_context_utilization():
    assert calculate_faithfulness(4, 5) == 0.8
    assert calculate_faithfulness(5, 5) == 1.0
    assert calculate_faithfulness(0, 5) == 0.0
    assert calculate_faithfulness(0, 0) == 1.0

    retrieved = ["doc1", "doc2", "doc3", "doc4"]
    cited = ["doc1", "doc3"]
    assert calculate_context_utilization(cited, retrieved) == 0.5
    assert calculate_context_utilization([], retrieved) == 0.0


def test_reorder_lost_in_the_middle():
    # 1. Edge cases
    assert reorder_lost_in_the_middle([]) == []
    assert reorder_lost_in_the_middle(["a"]) == ["a"]
    assert reorder_lost_in_the_middle(["a", "b"]) == ["a", "b"]

    # 2. 5 elements ranked [1, 2, 3, 4, 5]
    # Expected U-shape layout: [1, 3, 5, 4, 2]
    # Boundaries: 1 (start) and 2 (end), with 5 (worst) in the middle
    items = [1, 2, 3, 4, 5]
    reordered = reorder_lost_in_the_middle(items)
    assert reordered == [1, 3, 5, 4, 2]
    assert reordered[0] == 1  # Best at start
    assert reordered[-1] == 2  # 2nd best at end
    assert is_lost_in_the_middle_layout(reordered)

    # 3. 4 elements
    items4 = [1, 2, 3, 4]
    reordered4 = reorder_lost_in_the_middle(items4)
    assert reordered4 == [1, 3, 4, 2]
    assert is_lost_in_the_middle_layout(reordered4)


def test_build_ragas_dataset_normalizes_keys():
    raw_samples = [
        {
            "query": "Как взять отпуск?",
            "answer": "Подать заявление за 2 недели.",
            "contexts": ["Регламент отпусков: заявление подается за 2 недели."],
            "ground_truth": "Заявление за две недели.",
        },
        {
            "user_input": "Какой срок испытательного срока?",
            "response": "До 3 месяцев.",
            "retrieved_contexts": ["Испытательный срок составляет не более 3 месяцев."],
            "reference": "3 месяца.",
        },
    ]

    dataset = build_ragas_dataset(raw_samples)
    assert len(dataset) == 2

    # Check first sample normalized attributes
    s0 = dataset[0]
    assert s0.user_input == "Как взять отпуск?"
    assert s0.response == "Подать заявление за 2 недели."
    assert len(s0.retrieved_contexts) == 1
    assert "Регламент отпусков" in s0.retrieved_contexts[0]
    assert s0.reference == "Заявление за две недели."

    # Check second sample
    s1 = dataset[1]
    assert s1.user_input == "Какой срок испытательного срока?"
    assert s1.response == "До 3 месяцев."
    assert s1.reference == "3 месяца."

    # DataFrame conversion works cleanly
    df = dataset.to_pandas()
    assert "user_input" in df.columns
    assert "retrieved_contexts" in df.columns
    assert "response" in df.columns
    assert "reference" in df.columns


def test_evaluate_with_ragas_mocked(monkeypatch):
    from unittest.mock import MagicMock
    from ragas.evaluation import EvaluationResult

    fake_result = MagicMock()
    fake_result._repr_dict = {
        "faithfulness": 0.95,
        "answer_relevancy": 0.88,
    }

    mock_ragas_eval = MagicMock(return_value=fake_result)
    monkeypatch.setattr("viio_rag.evaluation.ragas_evaluate", mock_ragas_eval)

    samples = [
        {
            "user_input": "Тест?",
            "response": "Ответ.",
            "retrieved_contexts": ["Контекст."],
        }
    ]

    scores = evaluate_with_ragas(samples)
    assert scores["faithfulness"] == 0.95
    assert scores["answer_relevancy"] == 0.88
    assert mock_ragas_eval.called

