
"""End-to-end instruction attribution and hierarchical explanation pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sklearn.ensemble import RandomForestRegressor

from src.explainopt.instruction_attribution import (
    explain_graph,
    feature_vector,
    graph_for_ir,
    load_training_data,
)
from src.explainopt.hierarchical_explanation import (
    build_hierarchical_explanation,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "results" / "explanations" / "hierarchical_report.json"


def generate_hierarchical_report(
    ir_file: str | Path,
    optimization_level: str,
    aggregation: str = "sum",
) -> dict[str, Any]:
    """Generate instruction scores and aggregate them into blocks/functions."""
    if optimization_level not in {"O0", "O2"}:
        raise ValueError("optimization_level must be 'O0' or 'O2'")

    if aggregation not in {"sum", "mean"}:
        raise ValueError("aggregation must be 'sum' or 'mean'")

    ir_path = Path(ir_file)
    if not ir_path.is_file():
        raise FileNotFoundError(f"LLVM IR file not found: {ir_path}")

    X, y = load_training_data()
    model = RandomForestRegressor(
        n_estimators=300,
        min_samples_leaf=2,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X, y)

    graph = graph_for_ir(ir_path)
    attribution = explain_graph(graph, model, optimization_level)

    # Preserve missing effects as missing; never convert them to zero.
    scores = {
        row["node_id"]: row["attribution"]
        for row in attribution["instructions"]
        if row["attribution"] is not None
    }

    hierarchy = build_hierarchical_explanation(
        graph,
        scores,
        aggregation=aggregation,
    )

    return {
        "schema_version": 1,
        "input_ir": str(ir_path.resolve()),
        "optimization_level": optimization_level,
        "model": {
            "type": "RandomForestRegressor",
            "training_rows": len(y),
            "training_benchmarks": attribution.get("training_benchmarks"),
            "evaluation_warning": (
                "Model fitted on all available dataset rows; "
                "these are not held-out evaluation results."
            ),
        },
        "attribution": {
            "method": attribution["attribution_method"],
            "score_definition": attribution["score_definition"],
            "baseline_log_runtime_prediction": (
                attribution["baseline_log_runtime_prediction"]
            ),
            "baseline_runtime_prediction_seconds": (
                attribution["baseline_runtime_prediction_seconds"]
            ),
            "instruction_count": attribution["node_count"],
            "scored_instruction_count": len(scores),
            "unscored_instruction_ids": sorted(
                set(graph.nodes) - set(scores)
            ),
            "instructions": attribution["instructions"],
        },
        "hierarchy": hierarchy,
        "limitations": attribution["limitations"] + [
            "Block and function scores aggregate model perturbation scores; "
            "they are not independent causal effects.",
            "The sum aggregation is not guaranteed to equal the baseline "
            "runtime prediction or explain the full prediction.",
            "Removing a graph node is a feature perturbation, not an "
            "LLVM transformation or proof of semantic equivalence.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate instruction, basic-block, and function explanations."
    )
    parser.add_argument("ir_file", help="LLVM IR input file")
    parser.add_argument("--level", choices=["O0", "O2"], required=True)
    parser.add_argument(
        "--aggregation",
        choices=["sum", "mean"],
        default="sum",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    report = generate_hierarchical_report(
        args.ir_file,
        args.level,
        args.aggregation,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")

    hierarchy = report["hierarchy"]
    coverage = hierarchy["coverage"]
    print(f"Graph instructions: {coverage['graph_node_count']}")
    print(f"Scored instructions: {coverage['scored_instruction_count']}")
    print(f"Basic blocks explained: {len(hierarchy['basic_blocks'])}")
    print(f"Functions explained: {len(hierarchy['functions'])}")
    print(f"Complete attribution coverage: {coverage['complete']}")
    print(f"Report saved to: {output.resolve()}")


if __name__ == "__main__":
    main()
