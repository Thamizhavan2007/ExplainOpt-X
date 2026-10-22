
"""LLVM IR validation for ExplainOpt-X."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from src.explainopt.ir_parser import parse_ir


def find_clang() -> str | None:
    """Locate Clang through PATH or the configured Windows installation."""
    found = shutil.which("clang")
    if found:
        return found

    fallback = Path("C:/Program Files/LLVM/bin/clang.exe")
    return str(fallback) if fallback.is_file() else None


def validate_ir(
    ir_path: str | Path,
    clang_path: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Validate an IR file with Clang and inspect parsed structure."""
    path = Path(ir_path).resolve()

    report: dict[str, Any] = {
        "ir_file": str(path),
        "file_exists": path.is_file(),
        "llvm_validation_passed": False,
        "structural_validation_passed": False,
        "errors": [],
        "warnings": [],
        "tool": None,
    }

    if not path.is_file():
        report["errors"].append(f"IR file not found: {path}")
        return report

    try:
        module = parse_ir(path)
        report["warnings"].extend(module.warnings)
    except (OSError, UnicodeError, ValueError) as exc:
        report["errors"].append(f"IR parsing failed: {exc}")
        return report

    structural_errors = [
        warning
        for warning in module.warnings
        if "Unresolved branch target" in warning
        or "was not closed" in warning
    ]

    report["structural_validation_passed"] = (
        bool(module.functions) and not structural_errors
    )

    compiler = clang_path or find_clang()
    if not compiler:
        report["errors"].append(
            "Clang was not found. Install LLVM or provide clang_path."
        )
        return report

    report["tool"] = compiler

    try:
        # Compile the IR to a temporary object to make Clang parse and
        # verify the module without leaving build artifacts in the project.
        with tempfile.TemporaryDirectory(prefix="explainopt_validate_") as temp:
            output_file = Path(temp) / "validation.obj"

            completed = subprocess.run(
                [
                    compiler,
                    "-x", "ir",
                    "-c", str(path),
                    "-o", str(output_file),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )

        if completed.returncode == 0:
            report["llvm_validation_passed"] = True
        else:
            report["errors"].append(
                "Clang rejected the IR:\n"
                + (completed.stderr.strip() or completed.stdout.strip())
            )

        if completed.stderr.strip() and completed.returncode == 0:
            report["warnings"].append(completed.stderr.strip())

    except subprocess.TimeoutExpired:
        report["errors"].append(
            f"Clang validation timed out after {timeout_seconds} seconds."
        )
    except OSError as exc:
        report["errors"].append(f"Could not run Clang: {exc}")

    return report


def print_report(report: dict[str, Any]) -> None:
    """Print a readable validation report."""
    print(f"IR file: {report['ir_file']}")
    print(f"File exists: {report['file_exists']}")
    print(f"LLVM validation: {report['llvm_validation_passed']}")
    print(
        "Structural validation: "
        f"{report['structural_validation_passed']}"
    )

    if report["tool"]:
        print(f"Tool: {report['tool']}")

    for warning in report["warnings"]:
        print(f"WARNING: {warning}")

    for error in report["errors"]:
        print(f"ERROR: {error}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Validate LLVM IR.")
    parser.add_argument("ir_file", help="Path to an LLVM .ll file")
    parser.add_argument("--clang", help="Optional explicit Clang executable")
    args = parser.parse_args()

    result = validate_ir(args.ir_file, clang_path=args.clang)
    print_report(result)

    if not (
        result["llvm_validation_passed"]
        and result["structural_validation_passed"]
    ):
        raise SystemExit(1)
