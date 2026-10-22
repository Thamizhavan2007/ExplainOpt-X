
"""Train and evaluate a benchmark-level compiler speedup model."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import LeaveOneOut
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer

DATA_PATH = Path("data/processed/speedup_dataset.csv")
OUTPUT_DIR = Path("results/ml")

# Features available for each benchmark pair.
FEATURES = [
    "o0_instruction_nodes",
    "o2_instruction_nodes",
    "delta_instruction_nodes",
    "o0_graph_edges",
    "o2_graph_edges",
    "delta_graph_edges",
    "o0_data_dependency_edges",
    "o2_data_dependency_edges",
    "delta_data_dependency_edges",
    "o0_control_flow_edges",
    "o2_control_flow_edges",
    "delta_control_flow_edges",
]


def main():
    if not DATA_PATH.exists():
        raise FileNotFoundError(
            f"{DATA_PATH} not found. Run speedup_dataset first."
        )

    df = pd.read_csv(DATA_PATH)

    required = ["benchmark", "log_speedup", *FEATURES]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(
            "Missing required columns: "
            + ", ".join(missing)
            + "\nAvailable columns: "
            + ", ".join(df.columns)
        )

    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=["benchmark", "log_speedup"]).reset_index(drop=True)

    if len(df) < 3:
        raise ValueError(
            f"Only {len(df)} usable benchmark rows. "
            "At least 3 are required for this exploratory evaluation."
        )

    X = df[FEATURES].apply(pd.to_numeric, errors="coerce")
    y = pd.to_numeric(df["log_speedup"], errors="coerce")
    valid = y.notna()
    df, X, y = df.loc[valid].reset_index(drop=True), X.loc[valid].reset_index(drop=True), y.loc[valid].reset_index(drop=True)

    # Each held-out benchmark is excluded from training.
    loo = LeaveOneOut()
    predictions = []

    for train_idx, test_idx in loo.split(X):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

        model = make_pipeline(
            SimpleImputer(strategy="median"),
            RandomForestRegressor(
                n_estimators=200,
                max_features=1.0,
                min_samples_leaf=2,
                random_state=42,
            ),
        )
        model.fit(X_train, y_train)

        predicted = float(model.predict(X_test)[0])
        baseline = float(y_train.mean())

        predictions.append({
            "benchmark": str(df.iloc[test_idx[0]]["benchmark"]),
            "actual_log_speedup": float(y_test.iloc[0]),
            "predicted_log_speedup": predicted,
            "baseline_log_speedup": baseline,
            "actual_speedup": float(np.exp(y_test.iloc[0])),
            "predicted_speedup": float(np.exp(predicted)),
            "baseline_speedup": float(np.exp(baseline)),
        })

    pred_df = pd.DataFrame(predictions)
    y_true = pred_df["actual_log_speedup"].to_numpy()
    y_pred = pred_df["predicted_log_speedup"].to_numpy()
    y_base = pred_df["baseline_log_speedup"].to_numpy()

    def metrics(actual, predicted):
        return {
            "mae_log_speedup": float(mean_absolute_error(actual, predicted)),
            "rmse_log_speedup": float(
                np.sqrt(mean_squared_error(actual, predicted))
            ),
            "r2_log_speedup": (
                float(r2_score(actual, predicted))
                if len(actual) >= 2
                else None
            ),
        }

    report = {
        "evaluation": "Leave-One-Benchmark-Out",
        "benchmark_count": int(len(df)),
        "target": "log_speedup = log(runtime_O0 / runtime_O2)",
        "features": FEATURES,
        "random_forest": metrics(y_true, y_pred),
        "mean_baseline": metrics(y_true, y_base),
        "interpretation": (
            "Exploratory only: the benchmark count is small. "
            "Compare both models; lower MAE and RMSE are better. "
            "Positive R2 indicates improvement over predicting the test-set mean, "
            "but does not by itself establish generalization."
        ),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pred_path = OUTPUT_DIR / "speedup_predictions.csv"
    metrics_path = OUTPUT_DIR / "speedup_metrics.json"

    pred_df.to_csv(pred_path, index=False)
    metrics_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report, indent=2))
    print(f"\nPredictions saved to: {pred_path}")
    print(f"Metrics saved to: {metrics_path}")


if __name__ == "__main__":
    main()
