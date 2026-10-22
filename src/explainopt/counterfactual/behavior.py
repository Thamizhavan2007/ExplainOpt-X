
"""Differential behavior checks for ExplainOpt-X counterfactual candidates.

Compiles original and transformed LLVM IR into executables and compares
their exit codes and standard output for the same input cases.

Passing these tests is evidence of matching behavior for tested inputs,
not a formal proof of equivalence.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class BehaviorCase:
    arguments: list[str]
    original_returncode: int | None
    candidate_returncode: int | None
    original_stdout: str | None
    candidate_stdout: str | None
    original_stderr: str | None
    candidate_stderr: str | None
    passed: bool
    error: str | None = None


@dataclass
class BehaviorReport:
    passed: bool
    cases: list[BehaviorCase]
    formal_equivalence_proven: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _compile_ir(
    ir_text: str,
    output_path: Path,
    clang_path: str,
) -> tuple[bool, str | None]:
    with tempfile.TemporaryDirectory(prefix="explainopt_compile_") as tmp:
        ir_path = Path(tmp) / "program.ll"
        ir_path.write_text(ir_text, encoding="utf-8")

        try:
            result = subprocess.run(
                [clang_path, str(ir_path), "-o", str(output_path)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)

        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip()
            return False, message or "Clang compilation failed."

    return True, None


def compare_behavior(
    original_ir: str,
    candidate_ir: str,
    test_arguments: list[list[str]] | None = None,
    clang_path: str = "clang",
    timeout_seconds: int = 5,
) -> BehaviorReport:
    """Compare both executables for a list of command-line argument cases."""
    clang = shutil.which(clang_path)
    if clang is None:
        fallback = Path("C:/Program Files/LLVM/bin/clang.exe")
        if fallback.is_file():
            clang = str(fallback)
        else:
            return BehaviorReport(
                passed=False,
                cases=[
                    BehaviorCase(
                        arguments=[],
                        original_returncode=None,
                        candidate_returncode=None,
                        original_stdout=None,
                        candidate_stdout=None,
                        original_stderr=None,
                        candidate_stderr=None,
                        passed=False,
                        error="Clang executable not found.",
                    )
                ],
            )

    arguments = test_arguments if test_arguments is not None else [[]]

    with tempfile.TemporaryDirectory(prefix="explainopt_behavior_") as tmp:
        root = Path(tmp)
        original_exe = root / "original.exe"
        candidate_exe = root / "candidate.exe"

        ok_original, error_original = _compile_ir(
            original_ir, original_exe, clang
        )
        ok_candidate, error_candidate = _compile_ir(
            candidate_ir, candidate_exe, clang
        )

        if not ok_original or not ok_candidate:
            message = (
                f"Original compilation: {error_original}; "
                f"candidate compilation: {error_candidate}"
            )
            return BehaviorReport(
                passed=False,
                cases=[
                    BehaviorCase(
                        arguments=[],
                        original_returncode=None,
                        candidate_returncode=None,
                        original_stdout=None,
                        candidate_stdout=None,
                        original_stderr=None,
                        candidate_stderr=None,
                        passed=False,
                        error=message,
                    )
                ],
            )

        cases: list[BehaviorCase] = []

        for argv in arguments:
            try:
                original = subprocess.run(
                    [str(original_exe), *argv],
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
                candidate = subprocess.run(
                    [str(candidate_exe), *argv],
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                cases.append(
                    BehaviorCase(
                        arguments=argv,
                        original_returncode=None,
                        candidate_returncode=None,
                        original_stdout=None,
                        candidate_stdout=None,
                        original_stderr=None,
                        candidate_stderr=None,
                        passed=False,
                        error=str(exc),
                    )
                )
                continue

            passed = (
                original.returncode == candidate.returncode
                and original.stdout == candidate.stdout
                and original.stderr == candidate.stderr
            )

            cases.append(
                BehaviorCase(
                    arguments=argv,
                    original_returncode=original.returncode,
                    candidate_returncode=candidate.returncode,
                    original_stdout=original.stdout,
                    candidate_stdout=candidate.stdout,
                    original_stderr=original.stderr,
                    candidate_stderr=candidate.stderr,
                    passed=passed,
                )
            )

    return BehaviorReport(
        passed=bool(cases) and all(case.passed for case in cases),
        cases=cases,
        formal_equivalence_proven=False,
    )
