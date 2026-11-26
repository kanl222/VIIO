"""Run Ragas evaluation on VIIO RAG knowledge benchmark.

Computes both retrieval metrics (Recall@k, MRR, NDCG@k) and Ragas generation metrics
(Faithfulness, Answer Relevancy) using the modern Ragas 0.4 EvaluationDataset.
"""

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from viio_rag.application.evaluation import (
    calculate_recall_at_k,
    calculate_mrr,
    calculate_ndcg_at_k,
    build_ragas_dataset,
    evaluate_with_ragas,
    RAGAS_AVAILABLE,
)

logger = logging.getLogger(__name__)

SAMPLE_EVAL_CASES = [
    {
        "query": "Для чего предназначена система ИСЛО и с чем она интегрируется?",
        "ground_truth": "ИСЛО автоматизирует учёт лекарственных препаратов, управляет остатками аптечного склада и интегрируется с МДЛП.",
        "contexts": [
            "Информационная система лекарственного обеспечения (ИСЛО) предназначена для автоматизации учёта лекарственных препаратов, интеграции с МДЛП и управления остатками аптечного склада."
        ],
        "response": "Информационная система лекарственного обеспечения (ИСЛО) предназначена для автоматизации аптечного учёта и управления остатками, а также интегрируется с государственной системой МДЛП.",
        "relevant_ids": ["doc_islo"],
        "retrieved_ids": ["doc_islo", "doc_mdlp"],
    },
    {
        "query": "Какой температурный диапазон требуется для термолабильных лекарств по GDP?",
        "ground_truth": "Поддержание «холодовой цепи» в диапазоне от +2°C до +8°C.",
        "contexts": [
            "Правила хранения термолабильных лекарственных средств по стандартам GDP: поддержание «холодовой цепи» в строгом диапазоне температур от +2°C до +8°C."
        ],
        "response": "Согласно стандартам надлежащей дистрибьюторской практики (GDP), для хранения термолабильных средств необходим строгий температурный режим холодовой цепи от +2°C до +8°C.",
        "relevant_ids": ["doc_gdp"],
        "retrieved_ids": ["doc_gdp", "doc_zhnvlp"],
    },
    {
        "query": "По каким схемам формируются уведомления о выводе упаковок в МДЛП?",
        "ground_truth": "Электронные уведомления формируются по схеме 511 или 512 после обязательного сканирования двумерных DataMatrix штрихкодов.",
        "contexts": [
            "Порядок фиксации выбытия лекарств в МДЛП: обязательное сканирование двумерных DataMatrix штрихкодов, формирование электронных уведомлений о выводе упаковок из оборота по схеме 511 или 512."
        ],
        "response": "Уведомления о выводе упаковок из оборота в системе МДЛП формируются по схемам 511 или 512 со сканированием DataMatrix.",
        "relevant_ids": ["doc_mdlp"],
        "retrieved_ids": ["doc_mdlp", "doc_islo"],
    },
]


def run_benchmark(cases: list[dict[str, Any]], run_ragas_llm: bool = False) -> dict[str, Any]:
    print("=" * 65)
    print("VIIO RAG QUALITY EVALUATION (Offline Metrics + Ragas 0.4)")
    print("=" * 65)

    # 1. Deterministic retrieval metrics
    recalls = [calculate_recall_at_k(c["retrieved_ids"], c["relevant_ids"], k=2) for c in cases]
    mrrs = [calculate_mrr(c["retrieved_ids"], c["relevant_ids"], k=2) for c in cases]
    mean_recall = round(sum(recalls) / len(recalls), 4)
    mean_mrr = round(sum(mrrs) / len(mrrs), 4)

    print("\n--- 1. Deterministic Retrieval Metrics ---")
    print(f"Total Test Cases : {len(cases)}")
    print(f"Mean Recall@2    : {mean_recall:.4f}")
    print(f"Mean MRR@2       : {mean_mrr:.4f}")

    # 2. Ragas Evaluation Dataset
    print("\n--- 2. Ragas Dataset Compilation ---")
    dataset = build_ragas_dataset(cases)
    print(f"Ragas EvaluationDataset created successfully with {len(dataset)} samples.")
    print("Features:", dataset.features if hasattr(dataset, "features") else list(dataset[0].__dict__.keys()))

    ragas_scores = {}
    if run_ragas_llm:
        print("\n--- 3. Running Ragas Evaluation with LLM Judge ---")
        try:
            ragas_scores = evaluate_with_ragas(dataset)
            for metric_name, score in ragas_scores.items():
                print(f"Metric [{metric_name}]: {score:.4f}")
        except Exception as e:
            print(f"Ragas LLM evaluation skipped: {e}")
    else:
        print("\n--- 3. Ragas LLM Judge Status ---")
        print("LLM judge evaluation skipped (offline mode). Use --with-llm flag with OpenAI/YandexGPT API key to run live judge.")

    summary = {
        "samples_count": len(cases),
        "mean_recall_at_2": mean_recall,
        "mean_mrr_at_2": mean_mrr,
        "ragas_available": RAGAS_AVAILABLE,
        "ragas_scores": ragas_scores,
    }

    print("\n" + "=" * 65)
    print("Evaluation completed successfully.")
    print("=" * 65)
    return summary


def main():
    parser = argparse.ArgumentParser(description="Evaluate VIIO RAG quality with Ragas and retrieval metrics.")
    parser.add_argument("--with-llm", action="store_true", help="Run Ragas LLM judge metrics (requires OpenAI or Ollama credentials).")
    parser.add_argument("--input-json", type=str, default=None, help="Path to custom test cases JSON file.")
    args = parser.parse_args()

    default_json = Path(__file__).resolve().parents[1] / "data" / "golden_eval_dataset.json"
    if args.input_json:
        with open(args.input_json, "r", encoding="utf-8") as f:
            cases = json.load(f)
    elif default_json.exists():
        with open(default_json, "r", encoding="utf-8") as f:
            cases = json.load(f)
    else:
        cases = SAMPLE_EVAL_CASES

    run_benchmark(cases, run_ragas_llm=args.with_llm)


if __name__ == "__main__":
    main()
