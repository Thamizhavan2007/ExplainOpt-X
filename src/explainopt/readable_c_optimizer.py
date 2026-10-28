"""
Readable C optimization helper for ExplainOpt-X.
Conservative source-level identity simplifier; not a replacement for LLVM O2.
"""
import re

CHECKS = [
    ("Add zero", r"\b([A-Za-z_]\w*|\d+)\s*\+\s*0\b", "x + 0 → x", "Adding zero does not change an integer value."),
    ("Subtract zero", r"\b([A-Za-z_]\w*|\d+)\s*-\s*0\b", "x - 0 → x", "Subtracting zero does not change an integer value."),
    ("Multiply by one", r"\b([A-Za-z_]\w*|\d+)\s*\*\s*1\b", "x * 1 → x", "Multiplying by one does not change an integer value."),
    ("Divide by one", r"\b([A-Za-z_]\w*|\d+)\s*/\s*1\b", "x / 1 → x", "Dividing an integer by one preserves its value."),
    ("Constant folding candidate", r"\b\d+\s*[+*/-]\s*\d+\b", "literal arithmetic → constant", "Two literal operands may be folded by the compiler; this helper leaves them unchanged."),
    ("Constant propagation candidate", r"\b(?:int|long|short|char)\s+\w+\s*=\s*\d+\s*;", "literal assignment", "A literal initializer may enable propagation; usage analysis is needed."),
    ("Dead assignment candidate", r"\b\w+\s*=\s*\d+\s*;", "assignment may be dead", "Cannot safely remove without checking later uses; left unchanged."),
    ("Common subexpression candidate", r"\b\w+\s*[+*]\s*\w+\b", "repeated expression", "Repeated expressions may be reusable; side effects and data flow must be checked."),
    ("Strength reduction candidate", r"\b\w+\s*\*\s*(?:2|4|8|16)\b", "multiply by power of two", "Compiler may lower this efficiently; shifting is not always equivalent for signed/overflow cases."),
    ("Conditional simplification candidate", r"\bif\s*\(", "conditional", "Condition is present; simplifying it requires evaluating its logic."),
    ("Loop optimization candidate", r"\b(?:for|while)\s*\(", "loop", "Loop is present; transformation requires control-flow and dependency analysis."),
    ("Function inlining candidate", r"\bstatic\s+(?:inline\s+)?[\w*]+\s+\w+\s*\(", "static function", "May be eligible for inlining; actual decision depends on compiler heuristics."),
]

def optimize_readable_c(source):
    """Return (optimized_source, explanation_rows, check_rows)."""
    if not isinstance(source, str):
        source = str(source or "")
    # Preserve comments/strings while avoiding transformations inside them.
    token = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)
    protected = []
    def stash(m):
        protected.append(m.group(0))
        return f"__EXPLAINOPT_PROTECTED_{len(protected)-1}__"
    work = token.sub(stash, source)
    rules = [
        (re.compile(r"\b([A-Za-z_]\w*|\d+)\s*\+\s*0\b"), r"\1", "Add zero", "x + 0 → x", "Adding zero does not change the value for this simple integer-like expression."),
        (re.compile(r"\b([A-Za-z_]\w*|\d+)\s*-\s*0\b"), r"\1", "Subtract zero", "x - 0 → x", "Subtracting zero does not change the value."),
        (re.compile(r"\b([A-Za-z_]\w*|\d+)\s*\*\s*1\b"), r"\1", "Multiply by one", "x * 1 → x", "Multiplication by one does not change the value."),
        (re.compile(r"\b([A-Za-z_]\w*|\d+)\s*/\s*1\b"), r"\1", "Divide by one", "x / 1 → x", "Integer division by one preserves the value."),
    ]
    explanation_rows = []
    for pattern, replacement, label, transform, reason in rules:
        def apply(m):
            new = m.expand(replacement)
            explanation_rows.append({"Optimization": label, "Original expression": m.group(0), "Replacement": new, "Reason": reason, "Line": work.count("\n", 0, m.start()) + 1})
            return new
        work = pattern.sub(apply, work)
    for i, original in enumerate(protected):
        work = work.replace(f"__EXPLAINOPT_PROTECTED_{i}__", original)
    check_rows = []
    for name, pattern, output, reason in CHECKS:
        try:
            matches = list(re.finditer(pattern, source))
            check_rows.append({"Check": name, "Detected": bool(matches), "Occurrences": len(matches), "Meaning": reason})
        except re.error:
            check_rows.append({"Check": name, "Detected": False, "Occurrences": 0, "Meaning": "Pattern could not be evaluated."})
    return work, explanation_rows, check_rows
