
"""Instruction-level perturbation attribution for ExplainOpt-X.

The experiment measures changes in a Random Forest prediction when one
instruction node and its incident graph edges are removed from the
structural feature representation. It does not modify or execute LLVM IR.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import networkx as nx
import numpy as np
from sklearn.ensemble import RandomForestRegressor

from src.explainopt.ir_parser import parse_ir
from src.explainopt.program_graph import build_program_graph


ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data" / "processed" / "optimization_dataset.csv"
OUTPUT = ROOT / "results" / "explanations" / "instruction_attributions.json"

FEATURES = [
    "instruction_nodes",
    "graph_edges",
    "data_dependency_edges",
    "control_flow_edges",
    "optimization_level_is_O2",
]


def graph_for_ir(ir_path: str | Path) -> nx.MultiDiGraph:
    """Combine function graphs while keeping node IDs unique."""
    module = parse_ir(ir_path)
    combined = nx.MultiDiGraph()

    for function in module.functions:
        graph = build_program_graph(function)
        prefix = f"{function.name}:"
        renamed = nx.relabel_nodes(
            graph,
            {node: prefix + node for node in graph.nodes},
            copy=True,
        )
        combined = nx.compose(combined, renamed)

    if combined.number_of_nodes() == 0:
        raise ValueError(f"No instruction nodes found in {ir_path}")

    return combined


def feature_vector(
    graph: nx.MultiDiGraph,
    optimization_level: str,
) -> np.ndarray:
    """Produce features compatible with the existing runtime baseline."""
    edge_counts = {"data_dependency": 0, "control_flow": 0}

    for _, _, attrs in graph.edges(data=True):
        edge_type = attrs.get("edge_type")
        if edge_type in edge_counts:
            edge_counts[edge_type] += 1

    return np.array([[
        graph.number_of_nodes(),
        graph.number_of_edges(),
        edge_counts["data_dependency"],
        edge_counts["control_flow"],
        float(optimization_level == "O2"),
    ]], dtype=float)


def load_training_data() -> tuple[np.ndarray, np.ndarray]:
    """Load the existing dataset and train on its measured log-runtime target."""
    if not DATASET.is_file():
        raise FileNotFoundError(f"Dataset not found: {DATASET}")

    with DATASET.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise ValueError("Optimization dataset is empty.")

    if "output_consistent_with_other_level" in rows[0]:
        rows = [
            row for row in rows
            if row["output_consistent_with_other_level"].lower() == "true"
        ]

    X, y = [], []

    for row in rows:
        runtime = float(row["runtime_median_seconds"])
        if not np.isfinite(runtime) or runtime <= 0:
            continue

        X.append([
            float(row["instruction_nodes"]),
            float(row["graph_edges"]),
            float(row["data_dependency_edges"]),
            float(row["control_flow_edges"]),
            float(row["optimization_level"] == "O2"),
        ])
        y.append(np.log(runtime))

    if len(X) < 4:
        raise ValueError("At least four valid dataset rows are required.")

    return np.asarray(X), np.asarray(y)


def explain_graph(
    graph: nx.MultiDiGraph,
    model: RandomForestRegressor,
    optimization_level: str,
) -> dict:
    """Rank instruction nodes by their individual prediction perturbations."""
    original_features = feature_vector(graph, optimization_level)
    original_prediction = float(model.predict(original_features)[0])

    attributions = []

    for node_id, attrs in graph.nodes(data=True):
        masked_graph = graph.copy()
        masked_graph.remove_node(node_id)

        if masked_graph.number_of_nodes() == 0:
            masked_prediction = None
            effect = None
        else:
            masked_features = feature_vector(
                masked_graph, optimization_level
            )
            masked_prediction = float(
                model.predict(masked_features)[0]
            )
            # Positive means the node's presence raises predicted log-runtime.
            effect = original_prediction - masked_prediction

        attributions.append({
            "node_id": str(node_id),
            "function": attrs.get("function"),
            "block": attrs.get("block"),
            "line_number": attrs.get("line_number"),
            "opcode": attrs.get("opcode"),
            "instruction": attrs.get("text"),
            "baseline_log_runtime_prediction": original_prediction,
            "masked_log_runtime_prediction": masked_prediction,
            "attribution": effect,
            "method": "single_node_ablation",
        })

    attributions.sort(
        key=lambda row: (
            row["attribution"] is None,
            -abs(row["attribution"] or 0.0),
            row["node_id"],
        )
    )

    return {
        "schema_version": 1,
        "optimization_level": optimization_level,
        "baseline_log_runtime_prediction": original_prediction,
        "baseline_runtime_prediction_seconds": float(
            np.exp(original_prediction)
        ),
        "node_count": graph.number_of_nodes(),
        "attribution_method": "single_node_ablation",
        "score_definition": (
            "Baseline predicted log-runtime minus predicted log-runtime "
            "after removing one graph node and its incident edges."
        ),
        "limitations": [
            "The model is trained on only the available benchmark dataset.",
            "This is a descriptive perturbation of structural features, "
            "not a causal effect or a semantics-preserving IR rewrite.",
            "Correlated aggregate features can make node effects unstable.",
            "Single-node effects are not additive and need not sum to "
            "the baseline prediction.",
            "The final explanation model is fitted on all dataset rows; "
            "these scores do not constitute held-out model evaluation.",
        ],
        "instructions": attributions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate instruction-level model perturbation scores."
    )
    parser.add_argument("ir_file", help="LLVM IR input file")
    parser.add_argument(
        "--level", choices=["O0", "O2"], required=True,
        help="Optimization level corresponding to the input IR",
    )
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()

    X, y = load_training_data()
    model = RandomForestRegressor(
        n_estimators=300,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X, y)

    graph = graph_for_ir(args.ir_file)
    result = explain_graph(graph, model, args.level)
    result["input_ir"] = str(args.ir_file)
    result["training_rows"] = len(y)
    result["training_benchmarks"] = 8

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"Instructions explained: {result['node_count']}")
    print(
        "Predicted runtime (seconds): "
        f"{result['baseline_runtime_prediction_seconds']:.8f}"
    )
    print(f"Report saved to: {output}")


if __name__ == "__main__":
    main()
