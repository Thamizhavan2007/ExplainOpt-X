
"""Counterfactual LLVM IR transformations for ExplainOpt-X.

Supported transformations:
1. add-zero identity: %r = add i32 %x, 0
2. multiply-one identity: %r = mul i32 %x, 1

This is a constrained textual transformation. Candidate compilation and
differential testing do not constitute a formal equivalence proof.
"""

from __future__ import annotations

import re
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


SSA_NAME = r"%[-a-zA-Z$._0-9]+"

ADD_ZERO_RE = re.compile(
    rf"^(?P<indent>\s*)"
    rf"(?P<result>{SSA_NAME})"
    rf"\s*=\s*add\s+"
    rf"(?P<flags>(?:(?:nsw|nuw)\s+)*)"
    rf"(?P<ty>i\d+)\s+"
    rf"(?P<operand>{SSA_NAME}),\s*"
    rf"0\s*(?P<comment>;.*)?$"
)


MUL_ONE_RE = re.compile(
    r"^(?P<indent>[ \t]*)"
    r"(?P<result>%[-a-zA-Z$._0-9]+)"
    r"[ \t]*=[ \t]*mul[ \t]+"
    r"(?P<flags>(?:(?:nsw|nuw)[ \t]+)*)"
    r"(?P<ty>i\d+)[ \t]+"
    r"(?P<operand>%[-a-zA-Z$._0-9]+),[ \t]*1"
    r"[ \t]*(?P<comment>;.*)?$",
    re.MULTILINE,
)


TOKEN_RE = re.compile(r"%[-a-zA-Z$._0-9]+")


def _rewrite_identity(
    ir_text: str,
    pattern: re.Pattern,
    transformation_name: str,
    identity_description: str,
) -> tuple[str, list[TransformationRecord]]:
    """Apply supported identity rewrites within individual functions."""
    lines = ir_text.splitlines()
    changes: list[TransformationRecord] = []

    function_start = None
    i = 0

    while i < len(lines):
        line = lines[i]

        if re.match(r"^\s*define\b", line):
            function_start = i
        elif function_start is not None and line.strip() == "}":
            function_start = None

        match = pattern.match(line) if function_start is not None else None

        if not match:
            i += 1
            continue

        result_name = match.group("result")
        operand_name = match.group("operand")
        original = line

        function_end = i + 1
        while (
            function_end < len(lines)
            and lines[function_end].strip() != "}"
        ):
            function_end += 1

        function_lines = lines[function_start:function_end + 1]

        definition_count = sum(
            1
            for item in function_lines
            if re.match(
                rf"^\s*{re.escape(result_name)}\s*=",
                item,
            )
        )

        if definition_count != 1:
            changes.append(
                TransformationRecord(
                    transformation=transformation_name,
                    original_instruction=original,
                    replacement_instruction=original,
                    line_number=i + 1,
                    applied=False,
                    reason="SSA result definition is not unique.",
                )
            )
            i += 1
            continue

        # Replace only exact occurrences of the SSA result in this function.
        token_pattern = re.compile(
            rf"(?<![-a-zA-Z$._0-9])"
            rf"{re.escape(result_name)}"
            rf"(?![-a-zA-Z$._0-9])"
        )

        uses_replaced = 0

        for j in range(function_start, function_end + 1):
            if j == i:
                continue

            if token_pattern.search(lines[j]):
                lines[j] = token_pattern.sub(operand_name, lines[j])
                uses_replaced += 1

        del lines[i]

        changes.append(
            TransformationRecord(
                transformation=transformation_name,
                original_instruction=original,
                replacement_instruction=(
                    f"Replaced uses of {result_name} with {operand_name}; "
                    f"removed the {identity_description} instruction."
                ),
                line_number=i + 1,
                applied=True,
                reason=f"Replaced references in {uses_replaced} line(s).",
            )
        )

        function_end -= 1

        # Continue at the same index because the current line was removed.

    transformed = "\n".join(lines)
    if ir_text.endswith("\n"):
        transformed += "\n"

    return transformed, changes


def simplify_add_zero(
    ir_text: str,
) -> tuple[str, list[TransformationRecord]]:
    """Replace eligible add-with-zero instructions."""
    return _rewrite_identity(
        ir_text,
        ADD_ZERO_RE,
        "add_zero_identity",
        "add-zero",
    )


def simplify_mul_one(
    ir_text: str,
) -> tuple[str, list[TransformationRecord]]:
    """Replace eligible multiply-by-one instructions."""
    return _rewrite_identity(
        ir_text,
        MUL_ONE_RE,
        "mul_one_identity",
        "multiply-by-one",
    )


def simplify_supported_identities(
    ir_text: str,
) -> tuple[str, list[TransformationRecord]]:
    """Apply supported identity transformations in a deterministic order."""
    after_add, add_changes = simplify_add_zero(ir_text)
    after_mul, mul_changes = simplify_mul_one(after_add)

    return after_mul, add_changes + mul_changes


def validate_candidate(
    candidate_ir: str,
    clang_path: str | None = None,
) -> tuple[bool, bool, str | None]:
    """Validate candidate IR using ExplainOpt-X's existing validator."""
    from src.explainopt.validation import validate_ir

    with tempfile.TemporaryDirectory(prefix="explainopt_cf_") as tmp:
        ir_path = Path(tmp) / "candidate.ll"
        ir_path.write_text(candidate_ir, encoding="utf-8")

        report = validate_ir(ir_path, clang_path=clang_path)

    structural_ok = report["structural_validation_passed"]
    llvm_ok = report["llvm_validation_passed"]

    errors = report.get("errors", [])
    error_message = "\n".join(errors) if errors else None

    if not structural_ok and error_message is None:
        error_message = "Structural validation failed."

    if structural_ok and not llvm_ok and error_message is None:
        error_message = "LLVM validation failed."

    return structural_ok, llvm_ok, error_message


def evaluate_candidate(
    original_ir: str,
    candidate_ir: str,
    changes: list[TransformationRecord],
    transformation: str,
    clang_path: str = "clang",
) -> CandidateResult:
    structural_ok, compile_ok, error = validate_candidate(
        candidate_ir,
        clang_path,
    )

    return CandidateResult(
        transformation=transformation,
        original_ir=original_ir,
        candidate_ir=candidate_ir,
        changes=changes,
        structural_validation_passed=structural_ok,
        compilation_passed=compile_ok,
        behavior_check_passed=None,
        equivalence_proven=False,
        rejection_reason=error,
    )


def transform_ir(ir_text: str) -> CandidateResult:
    candidate_ir, changes = simplify_supported_identities(ir_text)

    transformations = sorted(
        {change.transformation for change in changes if change.applied}
    )
    transformation_name = (
        "+".join(transformations)
        if transformations
        else "supported_identity_transformations"
    )

    result = evaluate_candidate(
        original_ir=ir_text,
        candidate_ir=candidate_ir,
        changes=changes,
        transformation=transformation_name,
    )

    if not result.structural_validation_passed or not result.compilation_passed:
        result.rejection_reason = (
            result.rejection_reason or "Candidate failed validation."
        )

    return result
