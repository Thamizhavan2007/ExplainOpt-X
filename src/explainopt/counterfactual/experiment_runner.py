
"""Reusable counterfactual experiment runner for ExplainOpt-X."""

import re
import argparse
import json
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from src.explainopt.counterfactual.engine import (
    simplify_supported_identities,
    validate_candidate,
)
from src.explainopt.counterfactual.behavior import compare_behavior
from src.explainopt.counterfactual.function_behavior import (
    compare_function_behavior,
)
from src.explainopt.validation import find_clang


def compile_ir(ir_text, output_path, clang_path, timeout_seconds=30):
    """Compile LLVM IR text into an executable."""
    with tempfile.TemporaryDirectory(prefix="explainopt_compile_") as temp_dir:
        ir_path = Path(temp_dir) / "candidate.ll"
        ir_path.write_text(ir_text, encoding="utf-8")

        result = subprocess.run(
            [str(clang_path), "-O0", str(ir_path), "-o", str(output_path)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

    return {
        "passed": result.returncode == 0,
        "returncode": result.returncode,
        "stderr": result.stderr,
    }


def run_once(executable, timeout_seconds=10):
    """Run one executable and record its result and elapsed time."""
    start = time.perf_counter()

    try:
        result = subprocess.run(
            [str(executable)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return {
            "seconds": time.perf_counter() - start,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": None,
        }
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {
            "seconds": time.perf_counter() - start,
            "returncode": None,
            "stdout": None,
            "stderr": None,
            "error": str(exc),
        }


def measure(executable, repeats=5, timeout_seconds=10):
    """Collect repeated runtime measurements."""
    runs = [
        run_once(executable, timeout_seconds)
        for _ in range(repeats)
    ]

    valid = all(
        run["error"] is None and run["returncode"] is not None
        for run in runs
    )

    report = {
        "repetitions": repeats,
        "runs": runs,
        "valid": valid,
    }

    if valid:
        timings = [run["seconds"] for run in runs]
        report["median_seconds"] = statistics.median(timings)
        report["mean_seconds"] = statistics.mean(timings)
        report["stdev_seconds"] = (
            statistics.stdev(timings) if len(timings) > 1 else 0.0
        )

    return report


def summarize_runs(runs):
    """Summarize repeated execution measurements."""
    valid = all(
        run["error"] is None and run["returncode"] is not None
        for run in runs
    )

    summary = {
        "repetitions": len(runs),
        "valid": valid,
        "runs": runs,
    }

    if valid:
        timings = [run["seconds"] for run in runs]
        summary["median_seconds"] = statistics.median(timings)
        summary["mean_seconds"] = statistics.mean(timings)
        summary["stdev_seconds"] = (
            statistics.stdev(timings) if len(timings) > 1 else 0.0
        )

    return summary


def find_transformed_i32_function(ir_text, changes):
    """Find a single transformed function with signature i32(i32).

    Returns its name when all applied changes belong to one compatible
    function; otherwise returns None.
    """
    lines = ir_text.splitlines()
    applied = [change for change in changes if change.applied]

    if not applied:
        return None

    transformed_functions = set()
    current_function = None

    for line_number, line in enumerate(lines, start=1):
        if line.lstrip().startswith("define "):
            current_function = None
            match = re.search(
                r"\bdefine\s+(?:[^@]*\s)?i32\s+@([A-Za-z_][A-Za-z0-9_]*)"
                r"\s*\(\s*i32\s+%[A-Za-z_][A-Za-z0-9_]*\s*\)",
                line,
            )
            if match:
                current_function = match.group(1)

        if any(change.line_number == line_number for change in applied):
            if current_function is None:
                return None
            transformed_functions.add(current_function)

        if line.strip() == "}":
            current_function = None

    if len(transformed_functions) != 1:
        return None

    return next(iter(transformed_functions))


def run_experiment(
    input_path,
    repeats=15,
    output_path="results/counterfactual/experiment_report.json",
    clang_path=None,
    timeout_seconds=10,
):
    """Validate, compare, and benchmark one counterfactual transformation."""
    if repeats < 5:
        raise ValueError("Use at least 5 repetitions.")

    input_path = Path(input_path).resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"LLVM IR file not found: {input_path}")

    compiler = clang_path or find_clang()
    if not compiler:
        raise RuntimeError("Clang was not found.")

    original_ir = input_path.read_text(encoding="utf-8")
    candidate_ir, changes = simplify_supported_identities(original_ir)

    report = {
        "input_file": str(input_path),
        "transformation": "supported_identities",
        "compiler": str(compiler),
        "compiler_flags": ["-O0"],
        "repetitions_per_version": repeats,
        "transformation_applied": bool(changes),
        "applied_changes": [
            {
                "line_number": change.line_number,
                "original_instruction": change.original_instruction,
                "replacement_instruction": change.replacement_instruction,
                "applied": change.applied,
            }
            for change in changes
        ],
        "formal_equivalence_proven": False,
    }

    applied_changes = [
    change for change in changes if change.applied
    ]

    report["transformation_applied"] = bool(applied_changes)

    report["applied_changes"] = [
        {
            "line_number": change.line_number,
            "transformation": change.transformation,
            "original_instruction": change.original_instruction,
            "replacement_instruction": change.replacement_instruction,
            "applied": change.applied,
            "reason": change.reason,
        }
        for change in changes
    ]

    if not applied_changes:
        report["status"] = "no_candidate"
        report["reason"] = (
            "No supported identity transformation was successfully applied."
        )

    else:
        # validate_candidate returns a tuple, not a dictionary.
        structural_ok, compilation_ok, validation_error = validate_candidate(
            candidate_ir,
            clang_path=str(compiler),
        )

        report["candidate_validation"] = {
            "structural_validation_passed": structural_ok,
            "llvm_validation_passed": compilation_ok,
            "error": validation_error,
        }
        report["candidate_structural_validation_passed"] = structural_ok
        report["candidate_compilation_passed"] = compilation_ok

        if not (structural_ok and compilation_ok):
            report["status"] = "candidate_rejected"
            report["reason"] = "Candidate validation or compilation failed."

        else:
            behavior = compare_behavior(
                original_ir=original_ir,
                candidate_ir=candidate_ir,
                clang_path=str(compiler),
                timeout_seconds=timeout_seconds,
            )

            if isinstance(behavior, dict):
                behavior_passed = behavior.get("passed") is True
                report["behavior_check"] = behavior
            else:
                behavior_passed = (
                    getattr(behavior, "passed", False) is True
                )
                report["behavior_check"] = {
                    key: value
                    for key, value in vars(behavior).items()
                    if not key.startswith("_")
                }

            report["program_behavior_passed"] = behavior_passed
            function_name = find_transformed_i32_function(original_ir, changes)
            report["function_behavior_check_applicable"] = function_name is not None
            report["function_behavior_passed"] = None
            report["function_behavior_check"] = {
                "status": "skipped",
                "reason": (
                    "No single transformed function with supported signature i32(i32) "
                    "could be identified."
                ),
            }
            if function_name is not None:
                function_report = compare_function_behavior(
                    original_ir=original_ir,
                    candidate_ir=candidate_ir,
                    function_name=function_name,
                    clang_path=str(compiler),
                    timeout_seconds=timeout_seconds,
                )

                report["function_behavior_check"] = function_report.to_dict()
                report["function_behavior_passed"] = function_report.passed
                
            # The combined check passes only if the program check passes and,
            # when applicable, the function-level check also passes.
            behavior_passed = (
                behavior_passed
                and (
                    report["function_behavior_passed"] is None
                    or report["function_behavior_passed"] is True
                )
            )

            report["differential_behavior_check_passed"] = behavior_passed

            if not behavior_passed:
                report["status"] = "behavior_check_failed"
                report["reason"] = (
                    "Observed behavior differed or the behavior check failed."
                )

            else:
                with tempfile.TemporaryDirectory(
                    prefix="explainopt_experiment_"
                ) as temp_dir:
                    temp_dir = Path(temp_dir)
                    original_exe = temp_dir / "original.exe"
                    candidate_exe = temp_dir / "candidate.exe"

                    original_compile = compile_ir(
                        original_ir, original_exe, compiler
                    )
                    candidate_compile = compile_ir(
                        candidate_ir, candidate_exe, compiler
                    )

                    report["original_compilation_passed"] = (
                        original_compile["passed"]
                    )
                    report["candidate_benchmark_compilation_passed"] = (
                        candidate_compile["passed"]
                    )

                    if not (
                        original_compile["passed"]
                        and candidate_compile["passed"]
                    ):
                        report["status"] = "benchmark_compilation_failed"
                        report["compilation_errors"] = {
                            "original": original_compile["stderr"],
                            "candidate": candidate_compile["stderr"],
                        }

                    else:
                        # Warm up both programs before collecting timings.
                        run_once(original_exe, timeout_seconds)
                        run_once(candidate_exe, timeout_seconds)

                        original_runs = []
                        candidate_runs = []

                        # Alternate execution order to reduce ordering bias.
                        for index in range(repeats):
                            if index % 2 == 0:
                                order = [
                                    (original_exe, original_runs),
                                    (candidate_exe, candidate_runs),
                                ]
                            else:
                                order = [
                                    (candidate_exe, candidate_runs),
                                    (original_exe, original_runs),
                                ]

                            for executable, destination in order:
                                destination.append(
                                    run_once(executable, timeout_seconds)
                                )

                        original_report = summarize_runs(original_runs)
                        candidate_report = summarize_runs(candidate_runs)

                        report["original_measurement"] = original_report
                        report["candidate_measurement"] = candidate_report

                        outputs_match = all(
                            (
                                original["returncode"],
                                original["stdout"],
                                original["stderr"],
                            )
                            == (
                                candidate["returncode"],
                                candidate["stdout"],
                                candidate["stderr"],
                            )
                            for original, candidate in zip(
                                original_runs, candidate_runs
                            )
                        )

                        report["paired_observations_match"] = outputs_match

                        if (
                            original_report["valid"]
                            and candidate_report["valid"]
                        ):
                            original_median = original_report["median_seconds"]
                            candidate_median = candidate_report["median_seconds"]

                            report["speedup_original_over_candidate"] = (
                                original_median / candidate_median
                                if candidate_median > 0
                                else None
                            )

                            report["runtime_reduction_percent"] = (
                                (original_median - candidate_median)
                                / original_median
                                * 100
                                if original_median > 0
                                else None
                            )

                            report["status"] = (
                                "measured"
                                if outputs_match
                                else "measured_observations_differ"
                            )
                            report["interpretation"] = (
                                "A speedup above 1 means the candidate's "
                                "median runtime was lower in this run. "
                                "Timing does not prove formal equivalence "
                                "or a general performance benefit."
                            )
                        else:
                            report["status"] = "measurement_failed"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, default=str),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "status": report.get("status"),
                "transformation_applied": report["transformation_applied"],
                "speedup": report.get("speedup_original_over_candidate"),
                "formal_equivalence_proven": False,
                "report": str(output_path),
            },
            indent=2,
        )
    )

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Run an ExplainOpt-X counterfactual experiment."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to an LLVM IR (.ll) file.",
    )
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument(
        "--output",
        default="results/counterfactual/experiment_report.json",
    )
    parser.add_argument("--clang", default=None)

    args = parser.parse_args()

    run_experiment(
        input_path=args.input,
        repeats=args.repeats,
        output_path=args.output,
        clang_path=args.clang,
    )


if __name__ == "__main__":
    main()
