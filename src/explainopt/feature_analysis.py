
"""Exploratory feature analysis for ExplainOpt-X.

Reports Random Forest feature importance and Spearman association
with measured speedup. Results are descriptive, not causal.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline

DATA_PATH = Path("data/processed/speedup_dataset.csv")
OUTPUT_DIR = Path("results/explanations")

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
        raise FileNotFoundError(f"Dataset not found: {DATA_PATH}")

    df = pd.read_csv(DATA_PATH)

    required = ["benchmark", "log_speedup", *FEATURES]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            f"Missing columns: {missing}\n"
            f"Available columns: {df.columns.tolist()}"
        )

    df = df.replace([np.inf, -np.inf], np.nan)
    df["log_speedup"] = pd.to_numeric(
        df["log_speedup"], errors="coerce"
    )
    df = df.dropna(subset=["log_speedup"]).reset_index(drop=True)

    if len(df) < 4:
        raise ValueError(
            "At least four benchmark rows are required for this "
            "exploratory analysis."
        )

    X = df[FEATURES].apply(pd.to_numeric, errors="coerce")
    y = df["log_speedup"]

    model = make_pipeline(
        SimpleImputer(strategy="median"),
        RandomForestRegressor(
            n_estimators=300,
            min_samples_leaf=2,
            random_state=42,
        ),
    )
    model.fit(X, y)

    forest = model.named_steps["randomforestregressor"]

    # These importances describe the model fitted to the entire dataset.
    # They are exploratory and are not independent test-set explanations.
    importances = forest.feature_importances_

    rows = []
    for index, feature in enumerate(FEATURES):
        x_feature = X[feature]

        if x_feature.nunique(dropna=True) < 2:
            rho, p_value = np.nan, np.nan
        else:
            result = spearmanr(x_feature, y, nan_policy="omit")
            rho = float(result.statistic)
            p_value = float(result.pvalue)

        rows.append({
            "feature": feature,
            "random_forest_importance": float(importances[index]),
            "spearman_rho": rho,
            "spearman_p_value": p_value,
        })

    results = pd.DataFrame(rows).sort_values(
        "random_forest_importance", ascending=False
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "feature_importance.csv"
    json_path = OUTPUT_DIR / "feature_analysis_summary.json"

    results.to_csv(csv_path, index=False)

    summary = {
        "benchmark_count": int(len(df)),
        "target": "log_speedup = log(runtime_O0 / runtime_O2)",
        "method": [
            "Random Forest impurity-based feature importance",
            "Spearman rank correlation with measured log speedup",
        ],
        "top_features": results.head(5).to_dict(orient="records"),
        "limitations": [
            "Only eight benchmark examples are currently available.",
            "Feature importance is fitted on the same data being analyzed.",
            "Correlations and importances do not establish causation.",
            "Correlated graph features can divide or distort importance.",
            "Spearman p-values are exploratory with this sample size.",
        ],
    }

    json_path.write_text(
        json.dumps(summary, indent=2, allow_nan=False),
        encoding="utf-8",
    ) if not results[["spearman_rho", "spearman_p_value"]].isna().any().any() else json_path.write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )

    print("\nTOP FEATURES")
    print(results.head(8).to_string(index=False))
    print(f"\nSaved feature analysis: {csv_path}")
    print(f"Saved summary: {json_path}")


if __name__ == "__main__":
    main()
