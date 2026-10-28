"""Controlled local benchmark helpers for ExplainOpt-X C source output.

Uses GCC or Clang if installed. Runs only the supplied C program, in a temporary
folder, with a timeout and identical stdin for each compared executable.
"""
from __future__ import annotations
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def find_compiler(preference: str = "Auto") -> str | None:
    candidates = ["gcc", "clang"] if preference == "Auto" else [preference.lower()]
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _compile(compiler: str, source: str, out_path: Path, opt_level: str) -> dict[str, Any]:
    source_path = out_path.with_suffix(".c")
    source_path.write_text(source, encoding="utf-8")
    command = [compiler, "-std=c11", opt_level, str(source_path), "-o", str(out_path)]
    start = time.perf_counter()
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        elapsed = time.perf_counter() - start
        return {
            "success": proc.returncode == 0,
            "compile_seconds": elapsed,
            "binary_bytes": out_path.stat().st_size if proc.returncode == 0 and out_path.exists() else None,
            "error": (proc.stderr or proc.stdout).strip(),
            "command": command,
            "binary": out_path,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "compile_seconds": time.perf_counter() - start,
                "binary_bytes": None, "error": "Compilation timed out after 30 seconds.",
                "command": command, "binary": out_path}


def _run(binary: Path, stdin_text: str, repeats: int = 3) -> dict[str, Any]:
    durations: list[float] = []
    first_result = None
    try:
        for _ in range(repeats):
            start = time.perf_counter()
            proc = subprocess.run(
                [str(binary)], input=stdin_text, capture_output=True, text=True,
                timeout=5, check=False,
            )
            durations.append(time.perf_counter() - start)
            if first_result is None:
                first_result = proc
        median_seconds = statistics.median(durations)
        mean_seconds = statistics.mean(durations)
        variation = ((max(durations) - min(durations)) / mean_seconds * 100) if mean_seconds else 0.0
        return {"success": True, "returncode": first_result.returncode,
                "stdout": first_result.stdout, "stderr": first_result.stderr,
                "median_seconds": median_seconds, "runs": durations,
                "variation_percent": variation}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Program execution timed out after 5 seconds."}
    except OSError as exc:
        return {"success": False, "error": str(exc)}


def run_performance_benchmarks(original_source: str, optimized_source: str,
                               stdin_text: str = "", compiler_preference: str = "Auto",
                               repeats: int = 3) -> dict[str, Any]:
    """Compile and compare source variants with consistent settings and stdin."""
    compiler = find_compiler(compiler_preference)
    if not compiler:
        return {"available": False, "message": "Neither the selected compiler nor a supported compiler was found. Install GCC or Clang and ensure it is on PATH."}

    results: dict[str, Any] = {"available": True, "compiler": compiler, "repeats": repeats,
                               "configurations": [], "runtime_comparison": None,
                               "correctness": None, "robustness": None}
    compiled: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="explainoptx_bench_") as temp_dir:
        temp = Path(temp_dir)
        configs = [
            ("Original -O0", original_source, "-O0", "original_o0"),
            ("ExplainOpt-X output -O0", optimized_source, "-O0", "optimized_o0"),
            ("Original -O2", original_source, "-O2", "original_o2"),
            ("Original -O3", original_source, "-O3", "original_o3"),
            ("ExplainOpt-X output -O2", optimized_source, "-O2", "optimized_o2"),
        ]
        run_results: dict[str, dict[str, Any]] = {}
        row_by_key: dict[str, dict[str, Any]] = {}
        for label, source, level, key in configs:
            info = _compile(compiler, source, temp / key, level)
            compiled[key] = info
            row = {
                "Configuration": label,
                "Compile success": "Yes" if info["success"] else "No",
                "Compilation time (ms)": round(info["compile_seconds"] * 1000, 3),
                "Median runtime (ms)": "N/A",
                "Details": "" if info["success"] else (info["error"][:400] or "Compilation failed"),
            }
            if info["success"]:
                run_info = _run(info["binary"], stdin_text, repeats)
                run_results[key] = run_info
                if run_info["success"]:
                    row["Median runtime (ms)"] = round(run_info["median_seconds"] * 1000, 3)
                else:
                    row["Details"] = run_info.get("error", "Execution failed")
            row_by_key[key] = row
            results["configurations"].append(row)

        original_info, optimized_info = compiled["original_o0"], compiled["optimized_o0"]
        if original_info["success"] and optimized_info["success"]:
            original_run = run_results.get("original_o0", {"success": False, "error": "Original runtime unavailable"})
            optimized_run = run_results.get("optimized_o0", {"success": False, "error": "Optimized runtime unavailable"})
            if original_run["success"] and optimized_run["success"]:
                same_behavior = (original_run["returncode"] == optimized_run["returncode"]
                                 and original_run["stdout"] == optimized_run["stdout"]
                                 and original_run["stderr"] == optimized_run["stderr"])
                orig_t = original_run["median_seconds"]
                opt_t = optimized_run["median_seconds"]
                improvement = ((orig_t - opt_t) / orig_t * 100) if orig_t > 0 else None
                results["runtime_comparison"] = {
                    "original_median_seconds": orig_t, "optimized_median_seconds": opt_t,
                    "execution_time_improvement_percent": improvement,
                    "original_runtime_variation_percent": original_run["variation_percent"],
                    "optimized_runtime_variation_percent": optimized_run["variation_percent"],
                    "original_run": original_run, "optimized_run": optimized_run,
                }
                results["correctness"] = {
                    "passed": same_behavior, "rate_percent": 100.0 if same_behavior else 0.0,
                    "test_cases": 1,
                    "note": "One supplied stdin scenario only; this is not a proof of equivalence for all inputs.",
                }
            else:
                results["runtime_error"] = (original_run.get("error") if not original_run["success"]
                                             else optimized_run.get("error"))
        else:
            results["runtime_error"] = "Original and optimized source must both compile with -O0 before runtime/correctness comparison."

        successes = sum(1 for item in results["configurations"] if item["Compile success"] == "Yes")
        results["robustness"] = {
            "configuration_compile_success_rate_percent": successes / len(configs) * 100,
            "configurations_compiled": successes,
            "configurations_tested": len(configs),
            "runtime_repeat_count": repeats,
            "scope_note": "This measures one source program and repeated runs only. A multi-program benchmark suite is required to assess robustness across program categories.",
        }
    return results
