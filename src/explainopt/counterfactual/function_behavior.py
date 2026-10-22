
"""Function-level differential checks for i32 LLVM IR functions.

The checker removes the existing main definition, links each IR module
with a generated C harness, and compares the target function's results
for the same inputs.

Passing checks are dynamic evidence, not a formal equivalence proof.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class FunctionBehaviorReport:
    function_name: str
    test_values: list[int]
    original_results: list[int] | None
    candidate_results: list[int] | None
    passed: bool
    formal_equivalence_proven: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _remove_function_definition(ir_text: str, function_name: str) -> str:
    """Remove one LLVM function definition, preserving other IR."""
    lines = ir_text.splitlines(keepends=True)
    output: list[str] = []
    index = 0

    target = re.compile(
        rf"^\s*define\b.*@{re.escape(function_name)}\s*\("
    )

    while index < len(lines):
        if not target.search(lines[index]):
            output.append(lines[index])
            index += 1
            continue

        # Skip the complete function body by counting braces.
        brace_depth = 0
        saw_opening_brace = False

        while index < len(lines):
            line = lines[index]
            brace_depth += line.count("{")
            brace_depth -= line.count("}")
            if "{" in line:
                saw_opening_brace = True
            index += 1

            if saw_opening_brace and brace_depth <= 0:
                break

    return "".join(output)


def _resolve_clang(clang_path: str) -> str | None:
    found = shutil.which(clang_path)
    if found:
        return found

    fallback = Path("C:/Program Files/LLVM/bin/clang.exe")
    return str(fallback) if fallback.is_file() else None


def _run_function(
    ir_text: str,
    function_name: str,
    test_values: list[int],
    clang: str,
    timeout_seconds: int,
) -> list[int]:
    """Compile IR with a C harness and return the target function results."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function_name):
        raise ValueError("function_name must be a plain C-compatible symbol.")

    if not test_values:
        raise ValueError("At least one test value is required.")

    if any(value < -(2**31) or value > (2**31 - 1) for value in test_values):
        raise ValueError("All test values must fit in a signed 32-bit integer.")

    ir_without_main = _remove_function_definition(ir_text, "main")

    harness_values = ", ".join(str(value) for value in test_values)
    harness = f"""
#include <stdint.h>
#include <stdio.h>
#include <inttypes.h>

extern int32_t {function_name}(int32_t);

int main(void) {{
    int32_t inputs[] = {{{harness_values}}};
    size_t count = sizeof(inputs) / sizeof(inputs[0]);

    for (size_t i = 0; i < count; ++i) {{
        printf("%" PRId32 "\\n", {function_name}(inputs[i]));
    }}
    return 0;
}}
"""

    with tempfile.TemporaryDirectory(prefix="explainopt_function_check_") as tmp:
        root = Path(tmp)
        ir_path = root / "module.ll"
        harness_path = root / "harness.c"
        executable = root / "function_test.exe"

        ir_path.write_text(ir_without_main, encoding="utf-8")
        harness_path.write_text(harness, encoding="utf-8")

        compile_result = subprocess.run(
            [clang, str(ir_path), str(harness_path), "-o", str(executable)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if compile_result.returncode != 0:
            raise RuntimeError(
                "Compilation failed: "
                + (compile_result.stderr.strip() or compile_result.stdout.strip())
            )

        run_result = subprocess.run(
            [str(executable)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if run_result.returncode != 0:
            raise RuntimeError(
                f"Function harness exited with code {run_result.returncode}: "
                f"{run_result.stderr.strip()}"
            )

        try:
            results = [int(line) for line in run_result.stdout.splitlines()]
        except ValueError as exc:
            raise RuntimeError("Harness returned an invalid integer result.") from exc

        if len(results) != len(test_values):
            raise RuntimeError(
                f"Expected {len(test_values)} results but received {len(results)}."
            )

        return results


def compare_function_behavior(
    original_ir: str,
    candidate_ir: str,
    function_name: str,
    test_values: list[int] | None = None,
    clang_path: str = "clang",
    timeout_seconds: int = 5,
) -> FunctionBehaviorReport:
    """Compare a single i32(i32) function over explicit integer inputs."""
    values = (
        test_values
        if test_values is not None
        else [0, 1, -1, 2, -2, 40, -40, 2**31 - 1, -(2**31)]
    )

    clang = _resolve_clang(clang_path)
    if clang is None:
        return FunctionBehaviorReport(
            function_name, values, None, None, False,
            error="Clang executable not found.",
        )

    try:
        original_results = _run_function(
            original_ir, function_name, values, clang, timeout_seconds
        )
        candidate_results = _run_function(
            candidate_ir, function_name, values, clang, timeout_seconds
        )
    except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
        return FunctionBehaviorReport(
            function_name, values, None, None, False, error=str(exc)
        )

    return FunctionBehaviorReport(
        function_name=function_name,
        test_values=values,
        original_results=original_results,
        candidate_results=candidate_results,
        passed=original_results == candidate_results,
        formal_equivalence_proven=False,
    )
