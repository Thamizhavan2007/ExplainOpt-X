
"""Batch and tie-aware summary for sensitivity-analysis reports.

Attribution and degree top-k groups both use random tie resolution at the
selection boundary. Random groups provide a third baseline.

This is a model-prediction diagnostic. Graph-node deletion is not a valid
LLVM IR transformation and does not establish real execution-time impact.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_FRACTIONS = [0.05, 0.10, 0.20]


def _sample_top_k(
    ranking_scores: np.ndarray,
    values: np.ndarray,
    k: int,
    rng: np.random.Generator,
    trials: int,
) -> tuple[list[float], float, int, int]:
    """Sample top-k groups, randomly resolving ties at the boundary.

    Higher ranking scores are preferred. All nodes strictly above the
    boundary are selected; the remaining slots are sampled uniformly
    without replacement from nodes tied at the boundary.

    Returns:
        Trial means, boundary score, boundary tie size, slots sampled
        from the boundary tie.
    """
    if len(ranking_scores) != len(values):
        raise ValueError("Ranking scores and values must have equal lengths.")
    if not 1 <= k < len(ranking_scores):
        raise ValueError("k must be between 1 and number of nodes - 1.")
    if trials < 1:
        raise ValueError("trials must be at least 1.")
    if not np.all(np.isfinite(ranking_scores)):
        raise ValueError("Ranking scores must all be finite.")
    if not np.all(np.isfinite(values)):
        raise ValueError("Values must all be finite.")

    boundary = float(np.sort(ranking_scores)[::-1][k - 1])
    above = np.flatnonzero(ranking_scores > boundary)
    tied = np.flatnonzero(ranking_scores == boundary)
    slots = k - len(above)

    if slots < 1 or slots > len(tied):
        raise RuntimeError("Invalid top-k boundary tie calculation.")

    trial_means: list[float] = []
    for _ in range(trials):
        chosen_ties = rng.choice(tied, size=slots, replace=False)
        chosen = np.concatenate((above, chosen_ties))
        trial_means.append(float(np.mean(values[chosen])))

    return trial_means, boundary, len(tied), slots


def tie_aware_evaluate(
    report: dict[str, Any],
    tie_trials: int = 200,
    seed: int = 42,
) -> dict[str, Any]:
    """Compare attribution, degree, and random node selections."""
    if tie_trials < 1:
        raise ValueError("tie_trials must be at least 1.")

    node_rows = report.get("node_scores")
    if not isinstance(node_rows, list) or not node_rows:
        raise ValueError(
            "This report has no per-node scores. Regenerate it using "
            "sensitivity_analysis.py with the node_scores field."
        )

    valid: list[dict[str, Any]] = []
    for row in node_rows:
        if not isinstance(row, dict):
            continue

        try:
            attribution = float(row["abs_attribution"])
            sensitivity = float(row["abs_sensitivity"])
            degree = float(row["degree"])
        except (KeyError, TypeError, ValueError):
            continue

        if not all(math.isfinite(x) for x in (attribution, sensitivity, degree)):
            continue

        valid.append({
            "node_id": str(row.get("node_id", "")),
            "abs_attribution": attribution,
            "abs_sensitivity": sensitivity,
            "degree": degree,
        })

    if len(valid) < 2:
        raise ValueError("At least two valid per-node score rows are required.")

    scores = np.asarray([r["abs_attribution"] for r in valid], dtype=float)
    sensitivities = np.asarray(
        [r["abs_sensitivity"] for r in valid], dtype=float
    )
    degrees = np.asarray([r["degree"] for r in valid], dtype=float)

    # Independent deterministic random streams for the three methods.
    attribution_rng = np.random.default_rng(seed)
    degree_rng = np.random.default_rng(seed + 1)
    random_rng = np.random.default_rng(seed + 2)

    outputs: list[dict[str, Any]] = []

    for fraction in DEFAULT_FRACTIONS:
        k = min(max(1, math.ceil(len(valid) * fraction)), len(valid) - 1)

        attribution_values, attribution_boundary, attribution_tie_size, attribution_slots = (
            _sample_top_k(
                scores, sensitivities, k, attribution_rng, tie_trials
            )
        )

        degree_values, degree_boundary, degree_tie_size, degree_slots = (
            _sample_top_k(
                degrees, sensitivities, k, degree_rng, tie_trials
            )
        )

        # Random selection baseline, sampled with the same number of trials.
        random_values: list[float] = []
        for _ in range(tie_trials):
            chosen = random_rng.choice(len(valid), size=k, replace=False)
            random_values.append(float(np.mean(sensitivities[chosen])))

        attribution_mean = float(np.mean(attribution_values))
        degree_mean = float(np.mean(degree_values))
        random_mean = float(np.mean(random_values))

        outputs.append({
            "fraction": fraction,
            "nodes_selected": k,
            "tie_trials": tie_trials,

            "attribution_boundary_abs_score": attribution_boundary,
            "attribution_boundary_tie_size": attribution_tie_size,
            "attribution_slots_filled_from_boundary_tie": attribution_slots,
            "tie_aware_mean_abs_sensitivity": attribution_mean,
            "tie_aware_std_abs_sensitivity": float(
                np.std(attribution_values)
            ),
            "tie_aware_min_abs_sensitivity": float(
                np.min(attribution_values)
            ),
            "tie_aware_max_abs_sensitivity": float(
                np.max(attribution_values)
            ),

            "degree_boundary_score": degree_boundary,
            "degree_boundary_tie_size": degree_tie_size,
            "degree_slots_filled_from_boundary_tie": degree_slots,
            "degree_tie_aware_mean_abs_sensitivity": degree_mean,
            "degree_tie_aware_std_abs_sensitivity": float(
                np.std(degree_values)
            ),

            # Backward-compatible field name for existing report readers.
            "degree_top_k_mean_abs_sensitivity": degree_mean,

            "random_group_mean_abs_sensitivity": random_mean,
            "random_group_std_abs_sensitivity": float(
                np.std(random_values)
            ),

            "attribution_beats_degree": attribution_mean > degree_mean,
            "attribution_beats_random_mean": attribution_mean > random_mean,
            "attribution_over_random": (
                attribution_mean / random_mean if random_mean > 0 else None
            ),
            "tie_aware_over_random": (
                attribution_mean / random_mean if random_mean > 0 else None
            ),
            "zero_sensitivity": bool(
                np.all(sensitivities == 0)
            ),
        })

    return {
        "schema_version": 2,
        "experiment": "symmetric_tie_aware_sensitivity_reranking",
        "input_report": report.get("input_ir"),
        "optimization_level": report.get("optimization_level"),
        "node_count": len(valid),
        "tie_trials": tie_trials,
        "seed": seed,
        "tie_policy": (
            "Attribution and degree rankings both randomly resolve boundary "
            "ties without replacement; random groups are sampled independently."
        ),
        "results": outputs,
        "limitations": [
            "This evaluates model-prediction sensitivity, not measured runtime.",
            "Graph-node deletion is not a semantics-preserving LLVM IR transformation.",
            "Attribution and single-node sensitivity share a masking definition.",
            "Random tie resolution measures selection variability, not causal validity.",
            "The model is fitted on the available dataset; this is not held-out evaluation.",
            "Comparisons across fractions within a benchmark are not independent.",
        ],
    }


def summarize_existing_reports(report_dir: Path) -> list[dict[str, Any]]:
    """Summarize original sensitivity reports without modifying them."""
    rows: list[dict[str, Any]] = []

    for path in sorted(report_dir.glob("*_sensitivity.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rows.append({
                "benchmark": path.stem.removesuffix("_sensitivity"),
                "level": "",
                "status": "read_error",
                "details": str(exc),
            })
            continue

        ties = report.get("score_tie_diagnostics", {})
        ranking = report.get("ranking_diagnostics", {})

        for comp in report.get("group_comparisons", []):
            rows.append({
                "benchmark": path.stem.removesuffix("_sensitivity"),
                "level": report.get("optimization_level", ""),
                "status": "summary_only",
                "node_count": report.get("node_count"),
                "unique_abs_scores": ties.get(
                    "unique_absolute_attribution_scores"
                ),
                "tied_node_fraction": ties.get(
                    "fraction_nodes_in_tied_groups"
                ),
                "largest_tie_fraction": ties.get("largest_tie_fraction"),
                "spearman_attribution_sensitivity": ranking.get(
                    "spearman_abs_attribution_vs_abs_single_node_sensitivity"
                ),
                "fraction": comp.get("fraction"),
                "nodes_selected": comp.get("nodes_selected"),
                "attribution_mean_abs_sensitivity": comp.get(
                    "mean_abs_sensitivity_top_attribution"
                ),
                "degree_mean_abs_sensitivity": comp.get(
                    "mean_abs_sensitivity_top_degree"
                ),
                "random_mean_abs_sensitivity": comp.get("random_group_mean"),
                "attribution_over_random": comp.get("attribution_over_random"),
                "attribution_beats_degree": comp.get("attribution_beats_degree"),
                "attribution_beats_random_mean": comp.get(
                    "attribution_beats_random_mean"
                ),
            })

    return rows


def summarize_tie_aware_reports(report_dir: Path) -> list[dict[str, Any]]:
    """Create one summary row per benchmark and selection fraction."""
    rows: list[dict[str, Any]] = []

    for path in sorted(report_dir.glob("*_tie_aware.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rows.append({
                "benchmark": path.stem.removesuffix("_tie_aware"),
                "level": "",
                "status": "read_error",
                "details": str(exc),
            })
            continue

        benchmark = path.stem.removesuffix("_tie_aware")
        level = report.get("optimization_level", "")

        for result in report.get("results", []):
            attribution = result.get("tie_aware_mean_abs_sensitivity")
            degree = result.get("degree_tie_aware_mean_abs_sensitivity")
            random_mean = result.get("random_group_mean_abs_sensitivity")

            rows.append({
                "benchmark": benchmark,
                "level": level,
                "status": "ok",
                "node_count": report.get("node_count"),
                "fraction": result.get("fraction"),
                "nodes_selected": result.get("nodes_selected"),
                "tie_trials": result.get("tie_trials", report.get("tie_trials")),
                "attribution_mean_abs_sensitivity": attribution,
                "attribution_std": result.get("tie_aware_std_abs_sensitivity"),
                "degree_mean_abs_sensitivity": degree,
                "degree_std": result.get("degree_tie_aware_std_abs_sensitivity"),
                "random_mean_abs_sensitivity": random_mean,
                "random_std": result.get("random_group_std_abs_sensitivity"),
                "attribution_over_random": result.get("attribution_over_random"),
                "attribution_beats_degree": result.get("attribution_beats_degree"),
                "attribution_beats_random_mean": result.get(
                    "attribution_beats_random_mean"
                ),
                "zero_sensitivity": result.get("zero_sensitivity"),
            })

    return rows


def write_csv(rows: list[dict[str, Any]], output: Path) -> None:
    if not rows:
        raise SystemExit("No rows to write.")

    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})

    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Summary rows written: {len(rows)}")
    print(f"CSV saved to: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("results") / "explanations",
        help="Directory containing sensitivity and tie-aware reports",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output CSV path",
    )
    parser.add_argument(
        "--tie-aware-report",
        type=Path,
        default=None,
        help="Evaluate one sensitivity report containing per-node scores",
    )
    parser.add_argument("--tie-trials", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--summarize-tie-aware",
        action="store_true",
        help="Summarize existing *_tie_aware.json reports into a separate CSV",
    )
    args = parser.parse_args()

    args.report_dir.mkdir(parents=True, exist_ok=True)

    if args.tie_aware_report:
        if not args.tie_aware_report.is_file():
            raise SystemExit(
                f"Sensitivity report not found: {args.tie_aware_report}"
            )

        report = json.loads(
            args.tie_aware_report.read_text(encoding="utf-8")
        )
        result = tie_aware_evaluate(
            report,
            tie_trials=args.tie_trials,
            seed=args.seed,
        )

        target = args.report_dir / (
            f"{args.tie_aware_report.stem.removesuffix('_sensitivity')}"
            "_tie_aware.json"
        )
        target.write_text(
            json.dumps(result, indent=2),
            encoding="utf-8",
        )

        print(f"Tie-aware report saved to: {target}")
        for row in result["results"]:
            print(
                f"Top {row['fraction']:.0%} | "
                f"attribution={row['tie_aware_mean_abs_sensitivity']:.6f} "
                f"(SD {row['tie_aware_std_abs_sensitivity']:.6f}) | "
                f"degree={row['degree_tie_aware_mean_abs_sensitivity']:.6f} "
                f"(SD {row['degree_tie_aware_std_abs_sensitivity']:.6f}) | "
                f"random={row['random_group_mean_abs_sensitivity']:.6f}"
            )
        return

    if args.summarize_tie_aware:
        output = args.output or (
            args.report_dir / "tie_aware_batch_summary.csv"
        )
        rows = summarize_tie_aware_reports(args.report_dir)
        write_csv(rows, output)
        return

    output = args.output or (
        args.report_dir / "sensitivity_batch_summary.csv"
    )
    rows = summarize_existing_reports(args.report_dir)
    if not rows:
        raise SystemExit(
            f"No *_sensitivity.json reports found in {args.report_dir}"
        )

    report_files = len(list(args.report_dir.glob("*_sensitivity.json")))
    print(f"Sensitivity reports found: {report_files}")
    write_csv(rows, output)
    print(
        "Note: this CSV summarizes the original sensitivity reports. "
        "Use --summarize-tie-aware for tie-aware results."
    )


if __name__ == "__main__":
    main()
