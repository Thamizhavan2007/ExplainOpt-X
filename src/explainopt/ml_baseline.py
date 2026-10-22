
"""Benchmark-grouped ML baseline for ExplainOpt-X."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import GroupKFold
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.dummy import DummyRegressor

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data" / "processed" / "optimization_dataset.csv"
OUTPUT_DIR = ROOT / "results" / "ml"
METRICS_PATH = OUTPUT_DIR / "baseline_metrics.json"
PREDICTIONS_PATH = OUTPUT_DIR / "baseline_predictions.csv"

FEATURES = [
    "instruction_nodes",
    "graph_edges",
    "data_dependency_edges",
    "control_flow_edges",
]


def load_dataset():
    if not DATASET.exists():
        raise FileNotFoundError(
            f"Dataset not found: {DATASET}. Generate it first."
        )

    with DATASET.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    required = {
        "benchmark",
        "optimization_level",
        "runtime_median_seconds",
        *FEATURES,
    }

    if not rows:
        raise ValueError("Dataset is empty.")

    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"Dataset missing columns: {sorted(missing)}")

    # Output mismatches must not enter the training dataset.
    if "output_consistent_with_other_level" in rows[0]:
        rows = [
            row for row in rows
            if row["output_consistent_with_other_level"].lower() == "true"
        ]

    return rows


def main():
    rows = load_dataset()

    groups = np.array([row["benchmark"] for row in rows])
    levels = np.array([row["optimization_level"] for row in rows])

    # Include optimization level as a categorical feature.
    X_graph = np.array(
        [[float(row[name]) for name in FEATURES] for row in rows],
        dtype=float,
    )
    X_level = (levels == "O2").astype(float).reshape(-1, 1)
    X = np.hstack([X_graph, X_level])

    # Log-runtime stabilizes scale when runtimes differ greatly.
    y = np.log(
        np.array(
            [float(row["runtime_median_seconds"]) for row in rows],
            dtype=float,
        )
    )

    unique_groups = np.unique(groups)
    if len(unique_groups) < 4:
        raise ValueError(
            "At least four distinct benchmarks are required for grouped CV."
        )

    splitter = GroupKFold(n_splits=min(4, len(unique_groups)))
    predictions = np.full(len(rows), np.nan)
    baseline_predictions = np.full(len(rows), np.nan)

    for train_idx, test_idx in splitter.split(X, y, groups):
        model = RandomForestRegressor(
            n_estimators=300,
            min_samples_leaf=2,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X[train_idx], y[train_idx])
        predictions[test_idx] = model.predict(X[test_idx])

        # Baseline: predict the mean log-runtime from training rows.
        dummy = DummyRegressor(strategy="mean")
        dummy.fit(X[train_idx], y[train_idx])
        baseline_predictions[test_idx] = dummy.predict(X[test_idx])

    valid = ~np.isnan(predictions)
    y_true = y[valid]
    y_pred = predictions[valid]
    y_dummy = baseline_predictions[valid]

    metrics = {
        "evaluation": "GroupKFold by benchmark",
        "benchmark_count": int(len(unique_groups)),
        "row_count": int(len(rows)),
        "features": FEATURES + ["optimization_level_is_O2"],
        "target": "log(runtime_median_seconds)",
        "random_forest": {
            "mae_log_seconds": float(mean_absolute_error(y_true, y_pred)),
            "rmse_log_seconds": float(
                np.sqrt(mean_squared_error(y_true, y_pred))
            ),
            "r2_log_seconds": float(r2_score(y_true, y_pred)),
        },
        "mean_baseline": {
            "mae_log_seconds": float(mean_absolute_error(y_true, y_dummy)),
            "rmse_log_seconds": float(
                np.sqrt(mean_squared_error(y_true, y_dummy))
            ),
            "r2_log_seconds": float(r2_score(y_true, y_dummy)),
        },
        "warning": (
            "Exploratory results only: eight benchmark programs are too few "
            "to establish generalization. Runtime measurements are noisy."
        ),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(
        json.dumps(metrics, indent=2),
        encoding="utf-8",
    )

    with PREDICTIONS_PATH.open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "benchmark",
                "optimization_level",
                "actual_runtime_seconds",
                "predicted_runtime_seconds",
                "mean_baseline_runtime_seconds",
            ],
        )
        writer.writeheader()

        for i, row in enumerate(rows):
            writer.writerow({
                "benchmark": row["benchmark"],
                "optimization_level": row["optimization_level"],
                "actual_runtime_seconds": float(
                    row["runtime_median_seconds"]
                ),
                "predicted_runtime_seconds": float(np.exp(predictions[i])),
                "mean_baseline_runtime_seconds": float(
                    np.exp(baseline_predictions[i])
                ),
            })

    print(json.dumps(metrics, indent=2))
    print(f"\nMetrics saved to: {METRICS_PATH}")
    print(f"Predictions saved to: {PREDICTIONS_PATH}")


if __name__ == "__main__":
    main()
