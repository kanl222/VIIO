"""Local evaluation runner implementing the google-agents-cli-eval Quality Flywheel."""

from __future__ import annotations

import json
import os
import sys
import yaml
from pathlib import Path
from typing import Any


def load_dataset(dataset_path: Path) -> list[dict[str, Any]]:
    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("eval_cases", [])


def load_config(config_path: Path) -> dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_metric_functions(config: dict[str, Any]) -> dict[str, Any]:
    metrics = {}
    for custom in config.get("custom_metrics", []):
        name = custom["name"]
        code = custom.get("custom_function")
        if code:
            local_scope: dict[str, Any] = {}
            exec(code, globals(), local_scope)
            if "evaluate" in local_scope:
                metrics[name] = local_scope["evaluate"]
    return metrics


def run_evaluation(config_path: Path, dataset_path: Path) -> bool:
    print(f"\n==================================================")
    print(f"  Google Agents CLI Eval: Quality Flywheel Runner")
    print(f"==================================================")
    print(f"Config:  {config_path}")
    print(f"Dataset: {dataset_path}")

    config = load_config(config_path)
    eval_cases = load_dataset(dataset_path)
    metrics_to_run = config.get("metrics_to_run", [])
    thresholds = config.get("thresholds", {})
    metric_funcs = build_metric_functions(config)

    print(f"\nLoaded {len(eval_cases)} evaluation cases.")
    print(f"Metrics to evaluate: {', '.join(metrics_to_run)}")

    scores: dict[str, list[float]] = {m: [] for m in metrics_to_run}
    case_results: list[dict[str, Any]] = []

    for case in eval_cases:
        case_id = case.get("eval_case_id", "unknown")
        # Instance context passed to custom metric per google-agents-cli schema
        instance = {
            "eval_case_id": case_id,
            "category": case.get("category", ""),
            "expected_tool": case.get("expected_tool"),
            "prompt": case.get("prompt", {}),
            "response": case.get("responses", [{}])[0].get("response", {}) if case.get("responses") else {},
            "agent_data": case.get("agent_data", {}),
        }

        row: dict[str, Any] = {"case_id": case_id, "metrics": {}}

        for metric_name in metrics_to_run:
            fn = metric_funcs.get(metric_name)
            if fn:
                try:
                    res = fn(instance)
                    score = float(res.get("score", 0.0))
                    explanation = res.get("explanation", "")
                except Exception as e:
                    score = 0.0
                    explanation = f"Error: {e}"
            else:
                score = 1.0
                explanation = "Skipped"

            scores[metric_name].append(score)
            row["metrics"][metric_name] = (score, explanation)

        case_results.append(row)

    print("\n---------------- Per-Case Results ----------------")
    for r in case_results:
        print(f"\nCase: {r['case_id']}")
        for m, (s, expl) in r["metrics"].items():
            status = "PASS" if s >= thresholds.get(m, 0.0) else "FAIL"
            print(f"  - [{status}] {m:22}: {s:.2f} ({expl})")

    print("\n---------------- Evaluation Summary ----------------")
    all_passed = True
    for metric_name in metrics_to_run:
        vals = scores[metric_name]
        mean_score = sum(vals) / len(vals) if vals else 0.0
        required = thresholds.get(metric_name, 0.0)
        passed = mean_score >= required
        if not passed:
            all_passed = False
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {metric_name:25} Mean: {mean_score:.2f} / Required: {required:.2f}")

    verdict = "PASSED" if all_passed else "FAILED"
    print(f"\nFINAL VERDICT: {verdict}")
    print("==================================================\n")
    return all_passed


if __name__ == "__main__":
    base_dir = Path(__file__).parent
    cfg = base_dir / "eval_config.yaml"
    ds = base_dir / "datasets" / "viio_eval_dataset.json"
    ok = run_evaluation(cfg, ds)
    sys.exit(0 if ok else 1)
