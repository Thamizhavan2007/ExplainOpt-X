
"""Audit hierarchical explanation reports for completeness and consistency."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


def audit_report(report: dict[str, Any]) -> dict[str, Any]:
    """Return reproducible audit checks and warnings for a hierarchy report."""
    checks: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, details: str) -> None:
        checks.append({
            "name": name,
            "passed": bool(passed),
            "details": details,
        })

    attribution = report.get("attribution", {})
    hierarchy = report.get("hierarchy", {})
    coverage = hierarchy.get("coverage", {})
    instructions = attribution.get("instructions", [])
    blocks = hierarchy.get("basic_blocks", [])
    functions = hierarchy.get("functions", [])

    scores = [
        row.get("attribution")
        for row in instructions
        if row.get("attribution") is not None
    ]

    finite = all(
        isinstance(score, (int, float)) and math.isfinite(score)
        for score in scores
    )
    add_check(
        "finite_instruction_scores",
        finite,
        f"{len(scores)} scored instructions checked.",
    )

    graph_count = coverage.get("graph_node_count", 0)
    scored_count = coverage.get("scored_instruction_count", 0)
    complete = (
        coverage.get("complete") is True
        and graph_count == scored_count
        and graph_count == len(instructions)
    )
    add_check(
        "instruction_coverage",
        complete,
        f"Graph nodes={graph_count}, scored={scored_count}, "
        f"report instruction rows={len(instructions)}.",
    )

    node_ids = [row.get("node_id") for row in instructions]
    unique_ids = len(node_ids) == len(set(node_ids))
    add_check(
        "unique_instruction_ids",
        unique_ids,
        f"{len(node_ids)} rows; {len(set(node_ids))} unique node IDs.",
    )

    score_counts = Counter(
        float(score) for score in scores
        if isinstance(score, (int, float)) and math.isfinite(score)
    )
    repeated = max(score_counts.values(), default=0)
    repeated_fraction = repeated / len(scores) if scores else 0.0
    repetition_ok = repeated_fraction < 0.5
    add_check(
        "score_repetition",
        repetition_ok,
        f"Largest identical-score group: {repeated}/{len(scores)} "
        f"({repeated_fraction:.1%}). Repeated scores can result from "
        "the aggregate-feature model and are a diagnostic, not proof "
        "of an incorrect explanation.",
    )

    # Recompute the expected sum and mean from the instruction rows.
    # Missing scores are not silently treated as zero.
    score_by_id = {
        row["node_id"]: float(row["attribution"])
        for row in instructions
        if row.get("node_id") is not None
        and isinstance(row.get("attribution"), (int, float))
        and math.isfinite(row["attribution"])
    }

    expected_blocks: dict[tuple[str, str], list[float]] = {}
    for row in instructions:
        score = row.get("attribution")
        if score is None or not isinstance(score, (int, float)):
            continue
        if not math.isfinite(score):
            continue
        key = (str(row.get("function", "<unknown>")),
               str(row.get("block", "<unknown>")))
        expected_blocks.setdefault(key, []).append(float(score))

    aggregation = hierarchy.get("aggregation", "sum")
    block_consistent = True
    for block in blocks:
        key = (str(block.get("function", "<unknown>")),
               str(block.get("block", "<unknown>")))
        values = expected_blocks.get(key, [])
        if not values:
            block_consistent = False
            break
        expected = sum(values)
        if aggregation == "mean":
            expected /= len(values)
        actual = block.get("score")
        if not isinstance(actual, (int, float)) or not math.isclose(
            float(actual), expected, rel_tol=1e-8, abs_tol=1e-10
        ):
            block_consistent = False
            break

    add_check(
        "block_aggregation_consistency",
        block_consistent,
        f"Checked {len(blocks)} reported basic blocks using "
        f"aggregation={aggregation!r}.",
    )

    expected_functions: dict[str, list[float]] = {}
    for row in instructions:
        score = row.get("attribution")
        if score is None or not isinstance(score, (int, float)):
            continue
        if not math.isfinite(score):
            continue
        expected_functions.setdefault(
            str(row.get("function", "<unknown>")), []
        ).append(float(score))

    function_consistent = True
    for function in functions:
        values = expected_functions.get(str(function.get("function")), [])
        if not values:
            function_consistent = False
            break
        expected = sum(values)
        if aggregation == "mean":
            expected /= len(values)
        actual = function.get("score")
        if not isinstance(actual, (int, float)) or not math.isclose(
            float(actual), expected, rel_tol=1e-8, abs_tol=1e-10
        ):
            function_consistent = False
            break

    add_check(
        "function_aggregation_consistency",
        function_consistent,
        f"Checked {len(functions)} reported functions.",
    )

    warnings = []
    if not complete:
        warnings.append("Attribution coverage is incomplete.")
    if not finite:
        warnings.append("Non-finite or invalid instruction scores found.")
    if not unique_ids:
        warnings.append("Duplicate instruction node IDs found.")
    if not repetition_ok:
        warnings.append(
            "A single exact score occurs in at least half of scored "
            "instructions; inspect possible aggregate-feature aliasing."
        )
    if not block_consistent:
        warnings.append("Basic-block aggregation does not match instruction scores.")
    if not function_consistent:
        warnings.append("Function aggregation does not match instruction scores.")

    limitations = report.get("limitations", [])
    if not limitations:
        warnings.append("No explanation limitations are recorded.")

    return {
        "schema_version": 1,
        "audit_status": (
            "pass" if all(check["passed"] for check in checks) else "warning"
        ),
        "checks_passed": sum(check["passed"] for check in checks),
        "checks_total": len(checks),
        "checks": checks,
        "warnings": warnings,
        "limitations_present": bool(limitations),
        "interpretation": (
            "This audit checks report integrity and internal consistency. "
            "It does not establish causal attribution, semantic equivalence, "
            "or real-world runtime impact."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit a generated hierarchical explanation report."
    )
    parser.add_argument("report", help="Path to hierarchical_report.json")
    parser.add_argument(
        "--output",
        default=None,
        help="Audit JSON output path (default: sibling explanation_audit.json)",
    )
    args = parser.parse_args()

    report_path = Path(args.report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    audit = audit_report(report)

    output_path = (
        Path(args.output)
        if args.output
        else report_path.with_name("explanation_audit.json")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")

    print(f"Audit status: {audit['audit_status']}")
    print(f"Checks passed: {audit['checks_passed']}/{audit['checks_total']}")
    for warning in audit["warnings"]:
        print(f"WARNING: {warning}")
    print(f"Audit report saved to: {output_path.resolve()}")


if __name__ == "__main__":
    main()
