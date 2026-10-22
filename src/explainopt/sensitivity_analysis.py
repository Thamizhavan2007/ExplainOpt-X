"""Single-node sensitivity, attribution ties, and baseline ranking analysis.

This is a model-prediction diagnostic. Graph node deletion is not a valid
LLVM IR transformation and does not establish real execution-time impact.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestRegressor

from src.explainopt.instruction_attribution import (
    explain_graph,
    feature_vector,
    graph_for_ir,
    load_training_data,
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "results" / "explanations"


def _rank_nodes_by_score(nodes: list[Any], scores: dict[str, float]) -> list[Any]:
    return sorted(
        (node for node in nodes if str(node) in scores),
        key=lambda node: (-abs(scores[str(node)]), str(node)),
    )


def _safe_spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(set(left)) < 2 or len(set(right)) < 2:
        return None
    value = spearmanr(left, right).statistic
    return float(value) if value is not None and math.isfinite(float(value)) else None


def analyze(ir_path: Path, level: str, random_trials: int = 100, seed: int = 42) -> dict:
    if level not in {"O0", "O2"}:
        raise ValueError("Optimization level must be O0 or O2.")
    if random_trials < 1:
        raise ValueError("random_trials must be at least 1.")
    if not ir_path.is_file():
        raise FileNotFoundError(f"IR file not found: {ir_path}")

    X, y = load_training_data()
    model = RandomForestRegressor(
        n_estimators=300, min_samples_leaf=2, random_state=42
    )
    model.fit(X, y)

    graph = graph_for_ir(ir_path)
    explanation = explain_graph(graph, model, level)
    baseline_prediction = float(model.predict(feature_vector(graph, level))[0])
    nodes = list(graph.nodes)
    if len(nodes) < 2:
        raise ValueError("At least two instruction nodes are required.")

    attribution: dict[str, float] = {}
    for row in explanation.get("instructions", []):
        node_id = str(row.get("node_id"))
        score = row.get("attribution")
        if isinstance(score, (int, float)) and math.isfinite(float(score)):
            attribution[node_id] = float(score)

    # Independently recompute each one-node mask's model-prediction delta.
    sensitivity: dict[str, float] = {}
    for node in nodes:
        masked = graph.copy()
        masked.remove_node(node)
        prediction = float(model.predict(feature_vector(masked, level))[0])
        sensitivity[str(node)] = baseline_prediction - prediction

    scored_nodes = [node for node in nodes if str(node) in attribution]
    if not scored_nodes:
        raise ValueError("No finite attribution scores were produced.")

    # Degree heuristic: incident edge count, including MultiDiGraph multiplicity.
    degree_scores = {str(node): float(graph.degree(node)) for node in scored_nodes}
    attribution_ranked = _rank_nodes_by_score(scored_nodes, attribution)
    degree_ranked = _rank_nodes_by_score(scored_nodes, degree_scores)
    sensitivity_ranked = _rank_nodes_by_score(scored_nodes, sensitivity)

    attribution_abs = [abs(attribution[str(n)]) for n in scored_nodes]
    sensitivity_abs = [abs(sensitivity[str(n)]) for n in scored_nodes]
    degree_values = [degree_scores[str(n)] for n in scored_nodes]

    tie_counts = Counter(attribution_abs)
    tied_nodes = sum(count for count in tie_counts.values() if count > 1)
    largest_tie = max(tie_counts.values(), default=0)
    rng = np.random.default_rng(seed)
    fractions = [0.05, 0.10, 0.20]
    comparisons = []

    for fraction in fractions:
        k = min(max(1, math.ceil(len(scored_nodes) * fraction)), len(scored_nodes) - 1)
        top_nodes = attribution_ranked[:k]
        degree_nodes = degree_ranked[:k]
        top_change = float(np.mean([abs(sensitivity[str(n)]) for n in top_nodes]))
        degree_change = float(np.mean([abs(sensitivity[str(n)]) for n in degree_nodes]))

        random_means = []
        scored_list = list(scored_nodes)
        for _ in range(random_trials):
            selected = rng.choice(len(scored_list), size=k, replace=False)
            random_nodes = [scored_list[int(i)] for i in selected]
            random_means.append(
                float(np.mean([abs(sensitivity[str(n)]) for n in random_nodes]))
            )

        random_mean = float(np.mean(random_means))
        comparisons.append({
            "fraction": fraction,
            "nodes_selected": k,
            "mean_abs_sensitivity_top_attribution": top_change,
            "mean_abs_sensitivity_top_degree": degree_change,
            "random_group_mean": random_mean,
            "random_group_std": float(np.std(random_means)),
            "attribution_over_random": top_change / random_mean if random_mean > 0 else None,
            "attribution_beats_degree": top_change > degree_change,
            "attribution_beats_random_mean": top_change > random_mean,
        })

    return {
        "schema_version": 1,
        "experiment": "single_node_sensitivity_and_tie_analysis",
        "input_ir": str(ir_path),
        "optimization_level": level,
        "node_count": graph.number_of_nodes(),
        "scored_node_count": len(scored_nodes),
        "node_scores": [
            {
                "node_id": str(node),
                "abs_attribution": abs(attribution[str(node)]),
                "abs_sensitivity": abs(sensitivity[str(node)]),
                "degree": float(graph.degree(node)),
            }
            for node in scored_nodes
        ],
        "baseline_log_prediction": baseline_prediction,
        "score_tie_diagnostics": {
            "unique_absolute_attribution_scores": len(tie_counts),
            "unique_score_fraction": len(tie_counts) / len(attribution_abs),
            "nodes_in_tied_groups": tied_nodes,
            "fraction_nodes_in_tied_groups": tied_nodes / len(attribution_abs),
            "largest_identical_absolute_score_group": largest_tie,
            "largest_tie_fraction": largest_tie / len(attribution_abs),
        },
        "ranking_diagnostics": {
            "spearman_abs_attribution_vs_abs_single_node_sensitivity": _safe_spearman(
                attribution_abs, sensitivity_abs
            ),
            "spearman_abs_attribution_vs_node_degree": _safe_spearman(
                attribution_abs, degree_values
            ),
            "spearman_abs_degree_vs_abs_single_node_sensitivity": _safe_spearman(
                degree_values, sensitivity_abs
            ),
            "note": "Attribution is defined by single-node masking, so attribution-vs-sensitivity agreement is expected and is not independent validation.",
        },
        "group_comparisons": comparisons,
        "limitations": [
            "Node deletion can create invalid or unrealistic program graphs.",
            "All sensitivity values are changes in predicted log-runtime, not measured runtime.",
            "The fitted model uses aggregate graph features; attribution may mostly reflect graph-size changes.",
            "Attribution and single-node sensitivity share the same masking definition, so their agreement is not independent evidence.",
            "Degree is a simple baseline, not a semantics-aware explanation method.",
            "Random-group comparisons are exploratory and use a finite number of trials.",
            "The model is fit on the available dataset; this report is not held-out evaluation.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ir_file", type=Path)
    parser.add_argument("--level", choices=["O0", "O2"], required=True)
    parser.add_argument("--random-trials", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    report = analyze(args.ir_file, args.level, args.random_trials, args.seed)
    output = args.output or OUTPUT_DIR / f"{args.ir_file.stem}_sensitivity.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    ties = report["score_tie_diagnostics"]
    print(f"IR nodes: {report['node_count']}")
    print(f"Scored nodes: {report['scored_node_count']}")
    print(f"Unique absolute attribution scores: {ties['unique_absolute_attribution_scores']}")
    print(f"Nodes in tied groups: {ties['nodes_in_tied_groups']} ({ties['fraction_nodes_in_tied_groups']:.1%})")
    print(f"Largest tie group: {ties['largest_identical_absolute_score_group']}")
    print(f"Report saved to: {output}")
    for row in report["group_comparisons"]:
        ratio = row["attribution_over_random"]
        ratio_text = "undefined" if ratio is None else f"{ratio:.3f}"
        print(
            f"Top {row['fraction']:.0%} | attribution={row['mean_abs_sensitivity_top_attribution']:.6f} | "
            f"degree={row['mean_abs_sensitivity_top_degree']:.6f} | "
            f"random={row['random_group_mean']:.6f} | attr/random={ratio_text}"
        )


if __name__ == "__main__":
    main()
