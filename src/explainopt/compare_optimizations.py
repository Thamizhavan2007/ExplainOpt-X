
"""Compare measured optimization outcomes for ExplainOpt-X."""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "data" / "processed" / "optimization_dataset.csv"
OUTPUT = ROOT / "data" / "processed" / "optimization_comparison.csv"


def compare_optimizations() -> Path:
    if not INPUT.exists():
        raise FileNotFoundError(
            f"Dataset not found: {INPUT}. Run the dataset generator first."
        )

    with INPUT.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    grouped: dict[str, dict[str, dict]] = {}

    for row in rows:
        grouped.setdefault(row["benchmark"], {})[
            row["optimization_level"]
        ] = row

    comparisons = []

    for benchmark, levels in sorted(grouped.items()):
        if "O0" not in levels or "O2" not in levels:
            print(f"Skipping {benchmark}: missing O0 or O2 measurement.")
            continue

        o0 = levels["O0"]
        o2 = levels["O2"]

        t0 = float(o0["runtime_median_seconds"])
        t2 = float(o2["runtime_median_seconds"])
        size0 = int(o0["binary_size_bytes"])
        size2 = int(o2["binary_size_bytes"])

        if o0["output"] != o2["output"]:
            print(f"WARNING: output mismatch for {benchmark}")

        # Positive speedup means O2 ran faster than O0.
        speedup = t0 / t2 if t2 > 0 else None
        runtime_reduction_pct = (
            (t0 - t2) / t0 * 100 if t0 > 0 else None
        )
        size_change_pct = (
            (size2 - size0) / size0 * 100 if size0 > 0 else None
        )

        comparisons.append({
            "benchmark": benchmark,
            "o0_runtime_seconds": t0,
            "o2_runtime_seconds": t2,
            "runtime_speedup_o0_over_o2": speedup,
            "runtime_reduction_pct": runtime_reduction_pct,
            "o0_binary_size_bytes": size0,
            "o2_binary_size_bytes": size2,
            "binary_size_change_pct": size_change_pct,
            "o0_instruction_nodes": int(o0["instruction_nodes"]),
            "o2_instruction_nodes": int(o2["instruction_nodes"]),
            "instruction_node_change": (
                int(o2["instruction_nodes"])
                - int(o0["instruction_nodes"])
            ),
            "outputs_match": o0["output"] == o2["output"],
            "eligible_for_training": (
                o0["output"] == o2["output"]
                and t0 > 0
                and t2 > 0
            ),
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    fields = list(comparisons[0].keys()) if comparisons else [
        "benchmark", "o0_runtime_seconds", "o2_runtime_seconds"
    ]

    with OUTPUT.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(comparisons)

    print(f"Comparison saved to: {OUTPUT}")
    print(f"Benchmarks compared: {len(comparisons)}")

    for row in comparisons:
        print(
            f"{row['benchmark']}: "
            f"speedup={row['runtime_speedup_o0_over_o2']:.3f}x, "
            f"runtime reduction={row['runtime_reduction_pct']:.2f}%, "
            f"binary size change={row['binary_size_change_pct']:.2f}%"
        )

    return OUTPUT


if __name__ == "__main__":
    compare_optimizations()
