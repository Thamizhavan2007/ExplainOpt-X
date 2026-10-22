
"""Reproducible counterfactual benchmark for ExplainOpt-X."""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import time
from pathlib import Path

from src.explainopt.counterfactual.engine import (
    simplify_add_zero,
    validate_candidate,
)
from src.explainopt.counterfactual.behavior import compare_behavior
from src.explainopt.validation import find_clang


BENCHMARK_IR = r"""
@counter = global i32 0, align 4

define i32 @main() {
entry:
  br label %loop

loop:
  %i = phi i32 [ 0, %entry ], [ %next, %loop ]
  %v = add i32 %i, 0
  store volatile i32 %v, ptr @counter, align 4
  %next = add i32 %i, 1
  %cond = icmp slt i32 %next, 10000000
  br i1 %cond, label %loop, label %exit

exit:
  %final = load volatile i32, ptr @counter, align 4
  ret i32 0
}
"""


def compile_ir(ir_path: Path, exe_path: Path, clang: str) -> None:
    completed = subprocess.run(
        [clang, "-O0", str(ir_path), "-o", str(exe_path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Compilation failed for {ir_path}:\n"
            f"{completed.stderr or completed.stdout}"
        )


def run_once(executable: Path) -> dict:
    start = time.perf_counter()
    try:
        completed = subprocess.run(
            [str(executable)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        elapsed = time.perf_counter() - start

        return {
            "seconds": elapsed,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "execution_error": None,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "seconds": None,
            "returncode": None,
            "stdout": None,
            "stderr": None,
            "execution_error": str(exc),
        }


def summarize_runs(runs: list[dict]) -> dict:
    errors = [
        run["execution_error"]
        for run in runs
        if run["execution_error"] is not None
    ]
    if errors:
        raise RuntimeError(f"Benchmark execution failed: {errors}")

    timings = [run["seconds"] for run in runs]
    median = statistics.median(timings)
    deviations = [abs(value - median) for value in timings]

    return {
        "median_seconds": median,
        "mean_seconds": statistics.mean(timings),
        "stdev_seconds": (
            statistics.stdev(timings) if len(timings) > 1 else 0.0
        ),
        "median_absolute_deviation_seconds": statistics.median(deviations),
        "min_seconds": min(timings),
        "max_seconds": max(timings),
        "returncodes": [run["returncode"] for run in runs],
        "stdout_values": [run["stdout"] for run in runs],
        "stderr_values": [run["stderr"] for run in runs],
        "execution_errors": errors,
        "repetitions": len(runs),
    }


def main(repeats: int = 15) -> None:
    if repeats < 5:
        raise ValueError("Use at least 5 repetitions.")

    clang = find_clang()
    if not clang:
        raise RuntimeError("Clang was not found.")

    output_dir = Path("results/counterfactual")
    output_dir.mkdir(parents=True, exist_ok=True)

    original_ir = BENCHMARK_IR.strip() + "\n"
    candidate_ir, changes = simplify_add_zero(original_ir)
    applied_changes = [change for change in changes if change.applied]

    if not applied_changes:
        raise RuntimeError("No supported transformation was applied.")

    original_path = output_dir / "benchmark_original.ll"
    candidate_path = output_dir / "benchmark_candidate.ll"
    original_exe = output_dir / "benchmark_original.exe"
    candidate_exe = output_dir / "benchmark_candidate.exe"

    original_path.write_text(original_ir, encoding="utf-8")
    candidate_path.write_text(candidate_ir, encoding="utf-8")

    structural_ok, compile_ok, error = validate_candidate(
        candidate_ir, clang_path=clang
    )
    if not structural_ok or not compile_ok:
        raise RuntimeError(f"Candidate validation failed: {error}")

    behavior = compare_behavior(
        original_ir,
        candidate_ir,
        clang_path=clang,
    )
    if not behavior.passed:
        raise RuntimeError(
            "Differential behavior check failed. "
            "Refusing to benchmark the candidate."
        )

    compile_ir(original_path, original_exe, clang)
    compile_ir(candidate_path, candidate_exe, clang)

    # Warm up both programs; these executions are excluded from measurements.
    warmup_original = run_once(original_exe)
    warmup_candidate = run_once(candidate_exe)

    if warmup_original["execution_error"] or warmup_candidate["execution_error"]:
        raise RuntimeError("Warm-up execution failed.")

    if (
        warmup_original["returncode"] != 0
        or warmup_candidate["returncode"] != 0
    ):
        raise RuntimeError("A benchmark executable returned a nonzero status.")

    original_runs = []
    candidate_runs = []
    execution_order = []

    # Alternate order to reduce systematic first/second execution bias.
    for index in range(repeats):
        if index % 2 == 0:
            order = [
                ("original", original_exe, original_runs),
                ("candidate", candidate_exe, candidate_runs),
            ]
        else:
            order = [
                ("candidate", candidate_exe, candidate_runs),
                ("original", original_exe, original_runs),
            ]

        for name, executable, destination in order:
            result = run_once(executable)
            if result["execution_error"]:
                raise RuntimeError(
                    f"{name} execution failed: {result['execution_error']}"
                )
            if result["returncode"] != 0:
                raise RuntimeError(
                    f"{name} exited with status {result['returncode']}."
                )
            destination.append(result)

        execution_order.append([item[0] for item in order])

    original = summarize_runs(original_runs)
    candidate = summarize_runs(candidate_runs)

    # Verify output and exit status consistency across all repetitions.
    outputs_match = (
        len(set(original["returncodes"])) == 1
        and len(set(candidate["returncodes"])) == 1
        and original["returncodes"][0] == candidate["returncodes"][0]
        and len(set(original["stdout_values"])) == 1
        and len(set(candidate["stdout_values"])) == 1
        and original["stdout_values"][0] == candidate["stdout_values"][0]
        and len(set(original["stderr_values"])) == 1
        and len(set(candidate["stderr_values"])) == 1
        and original["stderr_values"][0] == candidate["stderr_values"][0]
    )

    if not outputs_match:
        raise RuntimeError(
            "Program output or exit status varied across repetitions."
        )

    original_median = original["median_seconds"]
    candidate_median = candidate["median_seconds"]
    speedup = original_median / candidate_median
    reduction_pct = (
        (original_median - candidate_median) / original_median * 100
    )

    version_result = subprocess.run(
        [clang, "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    report = {
        "transformation": "add_zero_identity",
        "compiler": clang,
        "compiler_version": (
            (version_result.stdout or version_result.stderr).splitlines()[0]
            if version_result.returncode == 0
            else "Version unavailable"
        ),
        "compiler_flags": ["-O0"],
        "repetitions_per_version": repeats,
        "execution_order": execution_order,
        "warmup_runs_excluded": 1,
        "applied_changes": [
            {
                "line_number": change.line_number,
                "original_instruction": change.original_instruction.strip(),
                "replacement_instruction": change.replacement_instruction,
            }
            for change in applied_changes
        ],
        "candidate_structural_validation_passed": structural_ok,
        "candidate_compilation_passed": compile_ok,
        "differential_behavior_check_passed": behavior.passed,
        "repeated_output_and_exit_status_consistent": outputs_match,
        "formal_equivalence_proven": False,
        "original_measurement": original,
        "candidate_measurement": candidate,
        "speedup_original_over_candidate": speedup,
        "runtime_reduction_percent": reduction_pct,
        "interpretation": (
            "Speedup above 1 indicates a lower candidate median in this run. "
            "Timing statistics describe observed variability, not a formal "
            "significance test or proof of a general performance benefit."
        ),
    }

    report_path = output_dir / "benchmark_report.json"
    report_path.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )

    print(json.dumps({
        "repetitions_per_version": repeats,
        "original_median_seconds": original_median,
        "candidate_median_seconds": candidate_median,
        "original_stdev_seconds": original["stdev_seconds"],
        "candidate_stdev_seconds": candidate["stdev_seconds"],
        "original_mad_seconds": original["median_absolute_deviation_seconds"],
        "candidate_mad_seconds": candidate["median_absolute_deviation_seconds"],
        "speedup": speedup,
        "runtime_reduction_percent": reduction_pct,
        "behavior_check": behavior.passed,
        "repeated_output_consistent": outputs_match,
        "formal_equivalence_proven": False,
        "report": str(report_path),
    }, indent=2))


if __name__ == "__main__":
    main()
