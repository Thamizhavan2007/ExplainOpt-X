
"""Build paired O0/O2 optimization features and measured speedup labels."""

from __future__ import annotations

import csv
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "data" / "processed" / "optimization_dataset.csv"
OUTPUT = ROOT / "data" / "processed" / "speedup_dataset.csv"

GRAPH_FEATURES = [
    "instruction_nodes",
    "graph_edges",
    "data_dependency_edges",
    "control_flow_edges",
]


def main() -> None:
    if not INPUT.exists():
        raise FileNotFoundError(
            f"Dataset not found: {INPUT}. Run the dataset generator first."
        )

    with INPUT.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    paired: dict[str, dict[str, dict[str, str]]] = {}

    for row in rows:
        benchmark = row["benchmark"]
        level = row["optimization_level"]
        paired.setdefault(benchmark, {})[level] = row

    output_rows = []

    for benchmark, levels in sorted(paired.items()):
        if "O0" not in levels or "O2" not in levels:
            print(f"Skipping {benchmark}: missing O0 or O2")
            continue

        o0, o2 = levels["O0"], levels["O2"]

        if o0["output"] != o2["output"]:
            print(f"Skipping {benchmark}: program outputs differ")
            continue

        if (
            o0.get("output_consistent_with_other_level", "true").lower()
            != "true"
            or o2.get("output_consistent_with_other_level", "true").lower()
            != "true"
        ):
            print(f"Skipping {benchmark}: output consistency check failed")
            continue

        t0 = float(o0["runtime_median_seconds"])
        t2 = float(o2["runtime_median_seconds"])

        if not (math.isfinite(t0) and math.isfinite(t2) and t0 > 0 and t2 > 0):
            print(f"Skipping {benchmark}: invalid runtime")
            continue

        row = {
            "benchmark": benchmark,
            "o0_runtime_seconds": t0,
            "o2_runtime_seconds": t2,
            "speedup_o0_over_o2": t0 / t2,
            "log_speedup": math.log(t0 / t2),
            "runtime_reduction_pct": (t0 - t2) / t0 * 100,
            "outputs_match": True,
        }

        for feature in GRAPH_FEATURES:
            value0 = float(o0[feature])
            value2 = float(o2[feature])

            row[f"o0_{feature}"] = value0
            row[f"o2_{feature}"] = value2
            row[f"delta_{feature}"] = value2 - value0
            row[f"relative_change_{feature}"] = (
                (value2 - value0) / value0 if value0 else 0.0
            )

        output_rows.append(row)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    fields = list(output_rows[0].keys()) if output_rows else [
        "benchmark", "o0_runtime_seconds", "o2_runtime_seconds",
        "speedup_o0_over_o2", "log_speedup",
    ]

    with OUTPUT.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Saved: {OUTPUT}")
    print(f"Paired benchmarks: {len(output_rows)}")
    print("Speedup > 1 means O2 was faster; speedup < 1 means O2 was slower.")
    print("This dataset is exploratory and is not sufficient for robust ML claims.")


if __name__ == "__main__":
    main()
