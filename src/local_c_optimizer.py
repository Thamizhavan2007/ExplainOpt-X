"""Offline, conservative C source optimizer for ExplainOpt-X.

This is a pattern-based educational tool, not a complete C parser/compiler.
It does not call an external API. Review and compile/test generated code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Callable


@dataclass
class Finding:
    technique: str
    status: str
    original_lines: str = "N/A"
    optimized_lines: str = "N/A"
    change: str = "No safe matching transformation found."
    reason: str = "No supported safe opportunity was identified by this local pattern-based pass."



TECHNIQUES = [
    "add_zero_identity", "sub_zero_identity", "mul_one_identity", "div_one_identity",
    "constant_folding", "constant_propagation", "dead_assignment",
    "common_subexpression", "strength_reduction", "conditional_simplification",
    "loop_optimization", "function_inlining",
]


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def optimize(source: str) -> tuple[str, list[dict]]:
    if not source.strip():
        raise ValueError("Input C code cannot be empty.")
    code = source.replace("\r\n", "\n")
    findings = {name: Finding(name, "not_applicable") for name in TECHNIQUES}

    def apply(name: str, pattern: str, repl: str | Callable, change: str, reason: str,
              flags: int = 0) -> None:
        nonlocal code
        matches = list(re.finditer(pattern, code, flags))
        if not matches:
            return
        before = code
        code = re.sub(pattern, repl, code, flags=flags)
        f = findings[name]
        f.status = "applied"
        f.original_lines = str(_line(before, matches[0].start()))
        f.optimized_lines = str(_line(code, matches[0].start()))
        f.change = change
        f.reason = reason

    # Narrow identity rules: simple identifiers or numeric literals only.
    atom = r"(?:[A-Za-z_]\w*|\d+(?:\.\d+)?)"
    apply("add_zero_identity", rf"\b({atom})\s*\+\s*0\b", r"\1",
          "Removed addition by zero", "For this simple matched expression, adding integer zero leaves the value unchanged.")
    apply("sub_zero_identity", rf"\b({atom})\s*-\s*0\b", r"\1",
          "Removed subtraction by zero", "For this simple matched expression, subtracting zero leaves the value unchanged.")
    apply("mul_one_identity", rf"\b({atom})\s*\*\s*1\b", r"\1",
          "Removed multiplication by one", "For this simple matched expression, multiplying by one leaves the value unchanged.")
    apply("div_one_identity", rf"\b({atom})\s*/\s*1\b", r"\1",
          "Removed division by one", "For this simple matched expression, division by one leaves the value unchanged.")

    # Basic non-negative integer literal folding, limited to + and *.
    def fold(m: re.Match) -> str:
        a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
        return str(a + b if op == "+" else a * b)
    apply("constant_folding", r"(?<![\w.])(\d+)\s*([+*])\s*(\d+)(?![\w.])", fold,
          "Folded integer-literal arithmetic", "Both operands are non-negative integer literals and the supported + or * operation can be evaluated directly.")

    # Constant-true conditional with a simple braced body (no nested braces).
    pattern = r"\bif\s*\(\s*1\s*\)\s*(\{[^{}]*\})"
    matches = list(re.finditer(pattern, code, re.S))
    if matches:
        before = code
        code = re.sub(pattern, lambda m: m.group(1), code, flags=re.S)
        f = findings["conditional_simplification"]
        f.status = "applied"
        f.original_lines = str(_line(before, matches[0].start()))
        f.optimized_lines = str(_line(code, matches[0].start()))
        f.change = "Removed a constant-true if wrapper"
        f.reason = "The body of if (1) executes unconditionally; this rule only handles a simple braced body without nested braces."

    # Simple adjacent integer constant propagation: int a = literal; int b = a op literal;
    prop = re.compile(
        r"\bint\s+([A-Za-z_]\w*)\s*=\s*(-?\d+)\s*;\s*\n"
        r"(\s*)int\s+([A-Za-z_]\w*)\s*=\s*\1\s*([+\-*])\s*(-?\d+)\s*;"
    )
    m = prop.search(code)
    if m:
        name, value, indent, target, op, rhs = m.groups()
        a, b = int(value), int(rhs)
        val = a + b if op == "+" else a - b if op == "-" else a * b
        before = code
        code = code[:m.start()] + f"int {name} = {value};\n{indent}int {target} = {val};" + code[m.end():]
        f = findings["constant_propagation"]
        f.status = "applied"
        f.original_lines = str(_line(before, m.start()))
        f.optimized_lines = str(_line(code, m.start()))
        f.change = f"Propagated constant {name} into initializer for {target}"
        f.reason = "The matched adjacent declarations use an integer literal and a simple arithmetic operation."

    # Explicitly report the techniques this regex-only tool does not attempt.
    advisories = {
        "dead_assignment": ("Not automatically transformed", "Removing assignments requires reliable use, scope, and side-effect analysis."),
        "common_subexpression": ("Not automatically transformed", "Reuse requires data-flow analysis and checks for intervening mutations or side effects."),
        "strength_reduction": ("Not automatically transformed", "Changing multiplication/division to shifts requires type, signedness, and value-range analysis."),
        "loop_optimization": ("Not automatically transformed", "Loop rewrites require control-flow and termination analysis."),
        "function_inlining": ("Not automatically transformed", "Inlining requires parsing function bodies, scopes, and call sites."),
    }
    for name, (change, reason) in advisories.items():
        if findings[name].status != "applied":
            findings[name].change = change
            findings[name].reason = reason

    return code, [asdict(findings[name]) for name in TECHNIQUES]


def create_report(optimized_code: str, findings: list[dict]) -> str:
    def cell(v):
        return str(v).replace("|", r"\|").replace("\n", " ")
    rows = [
        "# ExplainOpt-X Local Optimization Report", "",
        "> Offline pattern-based optimizer; not a complete C parser or formal verifier.", "",
        "## Optimized C code", "", "```c", optimized_code.rstrip(), "```", "",
        "## Optimization explanation", "",
        "| Technique | Status | Original line | Optimized line | Change | Reason |",
        "|---|---|---|---|---|---|",
    ]
    for f in findings:
        rows.append("| " + " | ".join(cell(f[k]) for k in (
            "technique", "status", "original_lines", "optimized_lines", "change", "reason"
        )) + " |")
    rows += ["", "Compile and test the result before relying on it. The report does not prove equivalence or measured speedup.", ""]
    return "\n".join(rows)
