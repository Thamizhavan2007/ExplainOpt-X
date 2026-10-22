
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "processed" / "optimization_dataset.csv"


def test_dataset_exists():
    assert DATASET.exists(), "Run python -m src.explainopt.dataset first"


def test_dataset_has_expected_benchmarks_and_levels():
    with DATASET.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    assert len(rows) >= 2

    benchmarks = {row["benchmark"] for row in rows}
    assert len(benchmarks) >= 8

    for benchmark in benchmarks:
        levels = {
            row["optimization_level"]
            for row in rows
            if row["benchmark"] == benchmark
        }
        assert {"O0", "O2"}.issubset(levels)


def test_measurements_are_valid():
    with DATASET.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    for row in rows:
        assert int(row["instruction_nodes"]) > 0
        assert int(row["binary_size_bytes"]) > 0
        assert float(row["runtime_median_seconds"]) > 0
        assert float(row["runtime_stdev_seconds"]) >= 0


def test_outputs_match_between_optimization_levels():
    with DATASET.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))

    for row in rows:
        assert row["output_consistent_with_other_level"].lower() == "true", (
            f"Output mismatch for {row['benchmark']} at "
            f"{row['optimization_level']}"
        )
