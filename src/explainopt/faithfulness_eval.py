
"""Evaluate attribution faithfulness against the runtime prediction model.

This measures model-prediction sensitivity, not real execution-time impact.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestRegressor

from src.explainopt.instruction_attribution import (
    explain_graph,
    feature_vector,
    graph_for_ir,
    load_training_data,
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "results" / "explanations"


def fit_model() -> RandomForestRegressor:
    """Fit the same model configuration used by instruction attribution."""
    X, y = load_training_data()
    model = RandomForestRegressor(
        n_estimators=300,
        min_samples_leaf=2,
        random_state=42,
    )
    model.fit(X, y)
    return model


def prediction_after_removal(
    graph,
    removed_nodes: list[str],
    model,
    level: str,
) -> float:
    """Predict log-runtime after removing nodes and incident graph edges."""
    masked = graph.copy()
    existing = [node for node in removed_nodes if node in masked]
    masked.remove_nodes_from(existing)

    if masked.number_of_nodes() == 0:
        raise ValueError("Removal eliminated every instruction node.")

    features = feature_vector(masked, level)
    return float(model.predict(features)[0])


def evaluate(
    ir_path: Path,
    level: str,
    random_trials: int = 30,
    seed: int = 42,
) -> dict:
    if level not in {"O0", "O2"}:
        raise ValueError("Optimization level must be O0 or O2.")
    if random_trials < 1:
        raise ValueError("random_trials must be at least 1.")
    if not ir_path.is_file():
        raise FileNotFoundError(f"IR file not found: {ir_path}")

    graph = graph_for_ir(ir_path)
    model = fit_model()
    explanation = explain_graph(graph, model, level)

    baseline = float(model.predict(feature_vector(graph, level))[0])
    nodes = list(graph.nodes)
    node_count = len(nodes)

    rows = explanation["instructions"]
    scores = {
        str(row["node_id"]): row.get("attribution")
        for row in rows
        if row.get("attribution") is not None
        and isinstance(row.get("attribution"), (int, float))
        and math.isfinite(row["attribution"])
    }

    # Rank by absolute single-node prediction perturbation.
    ranked = sorted(
        [node for node in nodes if str(node) in scores],
        key=lambda node: (-abs(scores[str(node)]), str(node)),
    )
    low_ranked = sorted(
        [node for node in nodes if str(node) in scores],
        key=lambda node: (abs(scores[str(node)]), str(node)),
    )

    if not ranked:
        raise ValueError("No finite instruction attribution scores found.")

    rng = np.random.default_rng(seed)
    fractions = [0.05, 0.10, 0.20, 0.30, 0.50]
    results = []

    for fraction in fractions:
        k = min(max(1, math.ceil(node_count * fraction)), node_count - 1)
        if k < 1:
            continue

        top_removed = ranked[:k]
        low_removed = low_ranked[:k]

        top_prediction = prediction_after_removal(
            graph, top_removed, model, level
        )
        low_prediction = prediction_after_removal(
            graph, low_removed, model, level
        )

        random_changes = []
        for _ in range(random_trials):
            chosen = rng.choice(nodes, size=k, replace=False).tolist()
            random_prediction = prediction_after_removal(
                graph, chosen, model, level
            )
            random_changes.append(abs(baseline - random_prediction))

        top_change = abs(baseline - top_prediction)
        low_change = abs(baseline - low_prediction)
        random_mean = float(np.mean(random_changes))
        random_std = float(np.std(random_changes))

        results.append({
            "fraction_removed": fraction,
            "nodes_removed": k,
            "baseline_log_prediction": baseline,
            "top_attribution_log_prediction": top_prediction,
            "low_attribution_log_prediction": low_prediction,
            "top_abs_prediction_change": top_change,
            "low_abs_prediction_change": low_change,
            "random_abs_change_mean": random_mean,
            "random_abs_change_std": random_std,
            "top_over_random_ratio": (
                top_change / random_mean if random_mean > 0 else None
            ),
        })

    return {
        "schema_version": 1,
        "experiment": "cumulative_node_removal_faithfulness",
        "input_ir": str(ir_path),
        "optimization_level": level,
        "node_count": node_count,
        "scored_node_count": len(ranked),
        "model": "RandomForestRegressor(n_estimators=300, min_samples_leaf=2, random_state=42)",
        "ranking": "descending absolute single-node attribution",
        "metric": "absolute change in predicted log-runtime after cumulative node removal",
        "random_trials_per_fraction": random_trials,
        "random_seed": seed,
        "results": results,
        "limitations": [
            "This evaluates sensitivity of the fitted model, not real execution time.",
            "Removing a graph node is not a semantics-preserving IR intervention.",
            "The model uses aggregate graph features, so many nodes may be indistinguishable.",
            "The model is trained on the available dataset; this is an exploratory diagnostic, not held-out validation.",
            "A large prediction change does not establish causal runtime impact.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate model-prediction faithfulness of instruction attributions."
    )
    parser.add_argument("ir_file", type=Path, help="Input LLVM IR file")
    parser.add_argument("--level", choices=["O0", "O2"], required=True)
    parser.add_argument("--random-trials", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    report = evaluate(
        args.ir_file,
        args.level,
        random_trials=args.random_trials,
        seed=args.seed,
    )

    output = args.output or (
        OUTPUT_DIR / f"{args.ir_file.stem}_faithfulness.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"IR nodes: {report['node_count']}")
    print(f"Scored nodes: {report['scored_node_count']}")
    print(f"Fractions evaluated: {len(report['results'])}")
    print(f"Report saved to: {output}")

    for row in report["results"]:
        ratio = row["top_over_random_ratio"]
        ratio_text = "undefined" if ratio is None else f"{ratio:.3f}"
        print(
            f"{row['fraction_removed']:.0%} removed | "
            f"top change={row['top_abs_prediction_change']:.6f} | "
            f"low change={row['low_abs_prediction_change']:.6f} | "
            f"random mean={row['random_abs_change_mean']:.6f} | "
            f"top/random={ratio_text}"
        )


if __name__ == "__main__":
    main()
