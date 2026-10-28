
"""Conservative LLVM IR optimization engine for ExplainOpt-X.

Supported transformations:
- add x, 0
- sub x, 0
- mul x, 1
- sdiv/udiv x, 1
- mul x, 0, when no overflow flags are present

This engine performs limited textual transformations. It does not prove
formal equivalence and is not a replacement for LLVM optimization passes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class TransformationRecord:
    transformation: str
    original_instruction: str
    replacement_instruction: str
    line_number: int
    applied: bool
    reason: str


@dataclass
class CandidateResult:
    transformation: str
    original_ir: str
    candidate_ir: str
    changes: list[TransformationRecord]
    structural_validation_passed: bool
    compilation_passed: bool
    behavior_check_passed: bool | None
    equivalence_proven: bool
    rejection_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


SSA = r"%[-a-zA-Z$._0-9]+"

IDENTITY_PATTERNS = [
    (
        "add_zero_identity",
        re.compile(
            rf"^(?P<indent>[ \t]*)(?P<result>{SSA})\s*=\s*"
            rf"add\s+(?:(?:nsw|nuw)\s+)*(?P<ty>i\d+)\s+"
            rf"(?P<operand>{SSA}),\s*0\s*(?:;.*)?$"
        ),
        "add-zero",
    ),
    (
        "sub_zero_identity",
        re.compile(
            rf"^(?P<indent>[ \t]*)(?P<result>{SSA})\s*=\s*"
            rf"sub\s+(?:(?:nsw|nuw)\s+)*(?P<ty>i\d+)\s+"
            rf"(?P<operand>{SSA}),\s*0\s*(?:;.*)?$"
        ),
        "subtract-zero",
    ),
    (
        "mul_one_identity",
        re.compile(
            rf"^(?P<indent>[ \t]*)(?P<result>{SSA})\s*=\s*"
            rf"mul\s+(?:(?:nsw|nuw)\s+)*(?P<ty>i\d+)\s+"
            rf"(?P<operand>{SSA}),\s*1\s*(?:;.*)?$"
        ),
        "multiply-by-one",
    ),
    (
        "div_one_identity",
        re.compile(
            rf"^(?P<indent>[ \t]*)(?P<result>{SSA})\s*=\s*"
            rf"(?:sdiv|udiv)\s+(?:exact\s+)*(?P<ty>i\d+)\s+"
            rf"(?P<operand>{SSA}),\s*1\s*(?:;.*)?$"
        ),
        "divide-by-one",
    ),
]


def _replace_uses(
    lines: list[str],
    start: int,
    end: int,
    definition_index: int,
    result_name: str,
    replacement: str,
) -> int:
    """Replace references to an SSA result within its function body."""
    token_pattern = re.compile(
        rf"(?<![-a-zA-Z$._0-9]){re.escape(result_name)}"
        rf"(?![-a-zA-Z$._0-9])"
    )
    count = 0

    for index in range(start, end):
        if index == definition_index:
            continue

        new_line, number = token_pattern.subn(replacement, lines[index])
        if number:
            lines[index] = new_line
            count += number

    return count


def _find_function_end(lines: list[str], start: int) -> int:
    """Return the index of a function's closing brace, or the final line."""
    for index in range(start + 1, len(lines)):
        if lines[index].strip() == "}":
            return index
    return len(lines) - 1


def _optimize_identity(
    ir_text: str,
    pattern: re.Pattern,
    name: str,
    description: str,
) -> tuple[str, list[TransformationRecord]]:
    """Apply one supported identity transformation inside function bodies."""
    lines = ir_text.splitlines()
    changes: list[TransformationRecord] = []
    index = 0

    while index < len(lines):
        if not re.match(r"^\s*define\b", lines[index]):
            index += 1
            continue

        start = index
        closing_brace = _find_function_end(lines, start)
        end = min(closing_brace + 1, len(lines))

        # Process from the bottom up so deleted lines do not invalidate
        # the indices of instructions that have not yet been examined.
        for line_index in range(end - 1, start, -1):
            match = pattern.match(lines[line_index])
            if not match:
                continue

            result = match.group("result")
            operand = match.group("operand")
            original = lines[line_index]

            definition_count = sum(
                1
                for line in lines[start:end]
                if re.match(rf"^\s*{re.escape(result)}\s*=", line)
            )

            if definition_count != 1:
                changes.append(
                    TransformationRecord(
                        transformation=name,
                        original_instruction=original,
                        replacement_instruction=original,
                        line_number=line_index + 1,
                        applied=False,
                        reason="Could not establish a unique SSA definition.",
                    )
                )
                continue

            uses = _replace_uses(
                lines,
                start,
                end,
                line_index,
                result,
                operand,
            )

            del lines[line_index]
            end -= 1

            changes.append(
                TransformationRecord(
                    transformation=name,
                    original_instruction=original,
                    replacement_instruction=(
                        f"Replace uses of {result} with {operand} and "
                        f"remove the {description} instruction."
                    ),
                    line_number=line_index + 1,
                    applied=True,
                    reason=f"Replaced {uses} SSA reference(s).",
                )
            )

        index = end

    output = "\n".join(lines)
    if ir_text.endswith("\n"):
        output += "\n"

    return output, changes


def simplify_add_zero(ir_text: str):
    return _optimize_identity(
        ir_text,
        IDENTITY_PATTERNS[0][1],
        "add_zero_identity",
        "add-zero",
    )


def simplify_sub_zero(ir_text: str):
    return _optimize_identity(
        ir_text,
        IDENTITY_PATTERNS[1][1],
        "sub_zero_identity",
        "subtract-zero",
    )


def simplify_mul_one(ir_text: str):
    return _optimize_identity(
        ir_text,
        IDENTITY_PATTERNS[2][1],
        "mul_one_identity",
        "multiply-by-one",
    )


def simplify_div_one(ir_text: str):
    return _optimize_identity(
        ir_text,
        IDENTITY_PATTERNS[3][1],
        "div_one_identity",
        "divide-by-one",
    )


def simplify_mul_zero(
    ir_text: str,
) -> tuple[str, list[TransformationRecord]]:
    """Replace an unflagged integer multiplication by zero."""
    pattern = re.compile(
        rf"^(?P<indent>[ \t]*)(?P<result>{SSA})\s*=\s*"
        rf"mul\s+(?P<ty>i\d+)\s+(?:{SSA}),\s*0\s*(?:;.*)?$"
    )

    lines = ir_text.splitlines()
    changes: list[TransformationRecord] = []
    index = 0

    while index < len(lines):
        if not re.match(r"^\s*define\b", lines[index]):
            index += 1
            continue

        start = index
        closing_brace = _find_function_end(lines, start)
        end = min(closing_brace + 1, len(lines))

        for line_index in range(end - 1, start, -1):
            match = pattern.match(lines[line_index])
            if not match:
                continue

            result = match.group("result")
            original = lines[line_index]

            definition_count = sum(
                1
                for line in lines[start:end]
                if re.match(rf"^\s*{re.escape(result)}\s*=", line)
            )
            if definition_count != 1:
                changes.append(
                    TransformationRecord(
                        "mul_zero_identity",
                        original,
                        original,
                        line_index + 1,
                        False,
                        "Could not establish a unique SSA definition.",
                    )
                )
                continue

            uses = _replace_uses(
                lines,
                start,
                end,
                line_index,
                result,
                "0",
            )

            del lines[line_index]
            end -= 1

            changes.append(
                TransformationRecord(
                    transformation="mul_zero_identity",
                    original_instruction=original,
                    replacement_instruction=(
                        f"Replace uses of {result} with integer constant 0."
                    ),
                    line_number=line_index + 1,
                    applied=True,
                    reason=(
                        f"Replaced {uses} SSA reference(s); "
                        "no nsw/nuw flags were present."
                    ),
                )
            )

        index = end

    output = "\n".join(lines)
    if ir_text.endswith("\n"):
        output += "\n"

    return output, changes


def simplify_supported_identities(
    ir_text: str,
) -> tuple[str, list[TransformationRecord]]:
    """Apply the supported identity transformations in a fixed order."""
    current = ir_text
    changes: list[TransformationRecord] = []

    passes = [
        simplify_add_zero,
        simplify_sub_zero,
        simplify_mul_one,
        simplify_div_one,
        simplify_mul_zero,
    ]

    for transform in passes:
        current, records = transform(current)
        changes.extend(records)

    return current, changes


def _resolve_clang(clang_path: str | None = None) -> str | None:
    """Find Clang using an explicit path, PATH, or the common Windows path."""
    if clang_path:
        found = shutil.which(clang_path)
        if found:
            return found

        explicit_path = Path(clang_path)
        if explicit_path.is_file():
            return str(explicit_path)

    found = shutil.which("clang")
    if found:
        return found

    fallback = Path(r"C:\Program Files\LLVM\bin\clang.exe")
    if fallback.is_file():
        return str(fallback)

    return None


def validate_candidate(
    candidate_ir: str,
    clang_path: str | None = None,
) -> tuple[bool, bool, str | None]:
    """Compile LLVM IR directly with Clang to check candidate validity.

    Returns:
        (structural_validation_passed, compilation_passed, error_message)

    This checks whether Clang accepts the IR. It does not establish
    behavioral equivalence with the original program.
    """
    clang = _resolve_clang(clang_path)
    if clang is None:
        return False, False, "Clang executable was not found."

    with tempfile.TemporaryDirectory(prefix="explainopt_ir_") as tmp:
        ir_path = Path(tmp) / "candidate.ll"
        obj_path = Path(tmp) / "candidate.obj"

        try:
            ir_path.write_text(candidate_ir, encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return False, False, f"Could not write candidate IR: {exc}"

        try:
            result = subprocess.run(
                [
                    clang,
                    "-x",
                    "ir",
                    "-c",
                    str(ir_path),
                    "-o",
                    str(obj_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, False, str(exc)

        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            return False, False, detail or "Clang rejected the LLVM IR."

        return True, True, None


def transform_ir(
    ir_text: str,
) -> tuple[str, list[TransformationRecord]]:
    """Compatibility wrapper used by the experiment runner."""
    return simplify_supported_identities(ir_text)


def evaluate_candidate(
    original_ir: str,
    candidate_ir: str,
    transformation: str = "supported_identity_optimizations",
    behavior_check_passed: bool | None = None,
    clang_path: str | None = None,
) -> CandidateResult:
    """Validate a candidate and return a structured result."""
    structural_ok, compilation_ok, rejection = validate_candidate(
        candidate_ir,
        clang_path,
    )

    return CandidateResult(
        transformation=transformation,
        original_ir=original_ir,
        candidate_ir=candidate_ir,
        changes=[],
        structural_validation_passed=structural_ok,
        compilation_passed=compilation_ok,
        behavior_check_passed=behavior_check_passed,
        equivalence_proven=False,
        rejection_reason=rejection,
    )
