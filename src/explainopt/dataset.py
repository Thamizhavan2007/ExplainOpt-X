
"""ExplainOpt-X optimization measurement dataset generator."""

from __future__ import annotations

import argparse
import csv
import shutil
import statistics
import subprocess
import time
from pathlib import Path

from src.explainopt.ir_parser import parse_ir
from src.explainopt.program_graph import build_program_graph, graph_summary


ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_DIR = ROOT / "samples" / "benchmarks"
RESULT_DIR = ROOT / "results" / "benchmarks"
IR_DIR = RESULT_DIR / "ir"
DATASET_PATH = ROOT / "data" / "processed" / "optimization_dataset.csv"

OPT_LEVELS = ("O0", "O2")
DEFAULT_REPEATS = 5
CONSOLE_LINKER_FLAG = "-Wl,/subsystem:console"


def find_clang() -> str:
    """Find Clang in PATH or its standard Windows installation directory."""
    compiler = shutil.which("clang")

    if compiler:
        return compiler

    windows_clang = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if windows_clang.exists():
        return str(windows_clang)

    raise FileNotFoundError(
        "Clang was not found. Install LLVM or add clang.exe to PATH."
    )


def run_command(command: list[str]) -> tuple[str, float]:
    """Run a command, return stdout and elapsed wall-clock seconds."""
    start = time.perf_counter()

    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    elapsed = time.perf_counter() - start

    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {result.returncode}):\n"
            f"{subprocess.list2cmdline(command)}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    return result.stdout.strip(), elapsed


def extract_graph_features(ir_path: Path) -> dict[str, int]:
    """Extract aggregated instruction-level graph features from LLVM IR."""
    module = parse_ir(ir_path)

    totals = {
        "function_count": len(module.functions),
        "instruction_nodes": 0,
        "graph_edges": 0,
        "data_dependency_edges": 0,
        "control_flow_edges": 0,
    }

    for function in module.functions:
        graph = build_program_graph(function)
        summary = graph_summary(graph)

        totals["instruction_nodes"] += summary["instruction_nodes"]
        totals["graph_edges"] += summary["total_edges"]
        totals["data_dependency_edges"] += summary["edge_counts"].get(
            "data_dependency", 0
        )
        totals["control_flow_edges"] += summary["edge_counts"].get(
            "control_flow", 0
        )

    return totals


def measure_runtime(
    executable: Path,
    repeats: int,
) -> tuple[list[float], str]:
    """Measure repeated process execution times and capture program output."""
    if repeats < 1:
        raise ValueError("repeats must be at least 1")

    timings: list[float] = []
    expected_output: str | None = None

    for run_index in range(repeats + 1):
        # First execution is a warm-up and is not included in the timings.
        output, elapsed = run_command([str(executable)])

        if expected_output is None:
            expected_output = output
        elif output != expected_output:
            raise RuntimeError(
                f"Inconsistent program output for {executable.name}: "
                f"{expected_output!r} versus {output!r}"
            )

        if run_index > 0:
            timings.append(elapsed)

    return timings, expected_output or ""


def generate_dataset(repeats: int = DEFAULT_REPEATS) -> Path:
    """Compile benchmarks at O0/O2, measure them, and save a CSV dataset."""
    if repeats < 1:
        raise ValueError("repeats must be at least 1")

    compiler = find_clang()

    for directory in (IR_DIR, RESULT_DIR, DATASET_PATH.parent):
        directory.mkdir(parents=True, exist_ok=True)

    sources = sorted(BENCHMARK_DIR.glob("*.c"))

    if not sources:
        raise FileNotFoundError(
            f"No benchmark .c files found in {BENCHMARK_DIR}"
        )

    rows: list[dict] = []

    for source in sources:
        print(f"\nBenchmark: {source.name}")

        outputs_by_level: dict[str, str] = {}

        for level in OPT_LEVELS:
            ir_path = IR_DIR / f"{source.stem}_{level}.ll"
            executable = RESULT_DIR / f"{source.stem}_{level}.exe"

            print(f"  Compiling with -{level}...")

            # Generate LLVM IR for graph analysis.
            _, ir_compile_seconds = run_command(
                [
                    compiler,
                    f"-{level}",
                    "-S",
                    "-emit-llvm",
                    str(source),
                    "-o",
                    str(ir_path),
                ]
            )

            # Build a runnable console executable.
            _, executable_compile_seconds = run_command(
                [
                    compiler,
                    f"-{level}",
                    CONSOLE_LINKER_FLAG,
                    str(source),
                    "-o",
                    str(executable),
                ]
            )

            graph_features = extract_graph_features(ir_path)

            timings, output = measure_runtime(executable, repeats)
            outputs_by_level[level] = output

            row = {
                "benchmark": source.stem,
                "source_file": str(source.relative_to(ROOT)),
                "optimization_level": level,
                **graph_features,
                "ir_compile_seconds": ir_compile_seconds,
                "executable_compile_seconds": executable_compile_seconds,
                "binary_size_bytes": executable.stat().st_size,
                "runtime_repeats": repeats,
                "runtime_median_seconds": statistics.median(timings),
                "runtime_min_seconds": min(timings),
                "runtime_stdev_seconds": (
                    statistics.stdev(timings) if len(timings) > 1 else 0.0
                ),
                "output": output,
                "output_consistent_with_other_level": "",
                "compiler": compiler,
            }

            rows.append(row)

            print(
                f"    Instructions: {graph_features['instruction_nodes']}, "
                f"edges: {graph_features['graph_edges']}"
            )
            print(
                f"    Median runtime: "
                f"{row['runtime_median_seconds']:.6f} seconds"
            )
            print(f"    Binary size: {row['binary_size_bytes']} bytes")

        outputs_match = outputs_by_level["O0"] == outputs_by_level["O2"]

        for row in rows:
            if row["benchmark"] == source.stem:
                row["output_consistent_with_other_level"] = outputs_match

        if not outputs_match:
            print(
                "  WARNING: O0 and O2 outputs differ. "
                "Investigate this benchmark before using its measurements."
            )
        else:
            print("  Output check: PASS")

    fieldnames = list(rows[0].keys())

    with DATASET_PATH.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDataset saved to: {DATASET_PATH}")
    print(f"Rows: {len(rows)}")
    print(f"Benchmarks: {len(sources)}")
    print(f"Optimization levels: {', '.join(OPT_LEVELS)}")

    return DATASET_PATH


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate ExplainOpt-X optimization measurements."
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_REPEATS,
        help=f"Timed executions per binary (default: {DEFAULT_REPEATS})",
    )
    args = parser.parse_args()

    generate_dataset(repeats=args.repeats)


if __name__ == "__main__":
    main()
