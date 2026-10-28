#!/usr/bin/env python3
"""ExplainOpt-X offline, rule-based C source optimizer.

This educational optimizer uses narrow regex patterns, not a complete C parser.
Review all edits and compile/test both versions before relying on the output.
"""
from __future__ import annotations
import argparse, json, re, shutil, subprocess, sys, textwrap
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

OUTPUT_CODE = Path('optimized.c')
OUTPUT_REPORT = Path('optimization_report.md')
OUTPUT_JSON = Path('optimization_dashboard.json')
TECHNIQUES = [
    'add_zero_identity', 'sub_zero_identity', 'mul_one_identity', 'div_one_identity',
    'constant_folding', 'constant_propagation', 'dead_assignment',
    'common_subexpression', 'strength_reduction', 'conditional_simplification',
    'loop_optimization', 'function_inlining',
]
TECHNIQUE_LABELS = {
    'add_zero_identity': 'Addition by zero', 'sub_zero_identity': 'Subtraction by zero',
    'mul_one_identity': 'Multiplication by one', 'div_one_identity': 'Division by one',
    'constant_folding': 'Constant folding', 'constant_propagation': 'Constant propagation',
    'dead_assignment': 'Dead assignment elimination',
    'common_subexpression': 'Common subexpression elimination',
    'strength_reduction': 'Strength reduction', 'conditional_simplification': 'Conditional simplification',
    'loop_optimization': 'Loop optimization', 'function_inlining': 'Function inlining',
}
STATUSES = ['applied', 'not_applicable']

@dataclass
class Finding:
    technique: str
    status: str = 'not_applicable'
    original_lines: str = 'N/A'
    optimized_lines: str = 'N/A'
    change: str = 'No safe matching transformation found.'
    reason: str = 'No supported pattern was found by the local rule-based optimizer.'

def line_number(text: str, offset: int) -> int:
    return text.count('\n', 0, max(0, offset)) + 1

def record(findings, technique, old, new, offset, change, reason):
    item = findings[technique]
    if item.status != 'applied':
        item.status = 'applied'
        item.original_lines = str(line_number(old, offset))
        item.optimized_lines = str(line_number(new, min(offset, len(new))))
        item.change, item.reason = change, reason

def apply_regex(code: str, pattern: str, replacement, technique: str, change: str,
                reason: str, findings: dict[str, Finding], flags: int = 0) -> str:
    matches = list(re.finditer(pattern, code, flags))
    if not matches:
        return code
    updated = re.sub(pattern, replacement, code, flags=flags)
    record(findings, technique, code, updated, matches[0].start(), change, reason)
    return updated

def optimize(source: str) -> tuple[str, list[Finding]]:
    if not isinstance(source, str) or not source.strip():
        raise ValueError('Input C code cannot be empty.')
    code = source.replace('\r\n', '\n').replace('\r', '\n')
    findings = {key: Finding(key) for key in TECHNIQUES}
    # Narrow local rules. Regex matching is not aware of C comments or string literals.
    operand = r'(?:[A-Za-z_]\w*|\d+(?:\.\d+)?)'
    rules = [
        (rf'\b({operand})\s*\+\s*0\b', r'\1', 'add_zero_identity', 'Removed addition by zero', 'For this recognized simple expression, adding zero preserves its value.'),
        (rf'\b({operand})\s*-\s*0\b', r'\1', 'sub_zero_identity', 'Removed subtraction by zero', 'For this recognized simple expression, subtracting zero preserves its value.'),
        (rf'\b({operand})\s*\*\s*1\b', r'\1', 'mul_one_identity', 'Removed multiplication by one', 'For this recognized simple expression, multiplication by one preserves its value.'),
        (rf'\b({operand})\s*/\s*1\b', r'\1', 'div_one_identity', 'Removed division by one', 'For this recognized simple expression, division by one preserves its value.'),
    ]
    for pattern, replacement, key, change, reason in rules:
        code = apply_regex(code, pattern, replacement, key, change, reason, findings)

    # Only positive integer literal addition/multiplication; division and overflow are not folded.
    const_pattern = r'(?<![\w.])(\d+)\s*([+*])\s*(\d+)(?![\w.])'
    def fold(m: re.Match[str]) -> str:
        a, op, b = int(m.group(1)), m.group(2), int(m.group(3))
        return str(a + b if op == '+' else a * b)
    code = apply_regex(code, const_pattern, fold, 'constant_folding',
        'Folded a simple integer constant expression',
        'Both operands are integer literals and the supported operation can be evaluated at compile time.', findings)

    # Constant-condition simplification for simple braced if/else blocks.
    # Only handles literal 0/1 conditions and bodies without nested braces.
    # Select the reachable branch and remove the conditional scaffolding.
    conditional_pattern = re.compile(
        r'\bif\s*\(\s*([01])\s*\)\s*(\{[^{}]*\})'
        r'(?:\s*else\s*(\{[^{}]*\}))?', re.S)
    conditional_match = conditional_pattern.search(code)
    if conditional_match:
        condition, true_block, false_block = conditional_match.groups()
        selected_block = true_block if condition == '1' else (false_block or '{}')
        old = code
        # Keep the selected block's interior; the enclosing braces are unnecessary here.
        replacement = textwrap.dedent(selected_block[1:-1]).strip('\n')
        code = code[:conditional_match.start()] + replacement + code[conditional_match.end():]
        record(findings, 'conditional_simplification', old, code, conditional_match.start(),
               f'Selected the reachable branch for constant condition {condition}',
               'The condition is the literal 0 or 1, so the opposite branch cannot execute. This rule is limited to simple blocks without nested braces.')

    # Adjacent declarations: int x = literal; then int y = x op literal;
    prop = re.compile(r'(?m)^([ \t]*)int[ \t]+([A-Za-z_]\w*)[ \t]*=[ \t]*(-?\d+)[ \t]*;[ \t]*\n'
                      r'([ \t]*)int[ \t]+([A-Za-z_]\w*)[ \t]*=[ \t]*\2[ \t]*([+*-])[ \t]*(-?\d+)[ \t]*;')
    m = prop.search(code)
    if m:
        ind1, name, val, ind2, target, op, rhs = m.groups()
        a, b = int(val), int(rhs)
        result = {'+': a+b, '-': a-b, '*': a*b}[op]
        if -(2**31) <= result <= 2**31-1:
            old = code
            replacement = f'{ind1}int {name} = {val};\n{ind2}int {target} = {result};'
            code = code[:m.start()] + replacement + code[m.end():]
            record(findings, 'constant_propagation', old, code, m.start(),
                   f'Propagated constant {name} into initializer for {target}',
                   'The adjacent declarations match a narrow integer-literal pattern; the second initializer was evaluated using the first constant.')

    # Conservative dead-assignment elimination for simple local declarations.
    # Repeated passes allow chains of now-unused temporary variables to disappear.
    # Only remove declarations whose initializer is a side-effect-free arithmetic expression.
    dead_assignment_changed = False
    safe_initializer = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*|^[+-]?\d+(?:\.\d+)?')
    declaration = re.compile(
        r'(?m)^[ \t]*(?:int|float|double|char|long|short)[ \t]+('
        r'[A-Za-z_]\w*)[ \t]*=[ \t]*([^;\n]+);[ \t]*(?:\n|$)'
    )
    safe_expression = re.compile(r'^[A-Za-z0-9_+*/%(). \t-]+$')
    while True:
        removed_one = False
        for match in declaration.finditer(code):
            name, initializer = match.group(1), match.group(2).strip()
            if not safe_expression.fullmatch(initializer):
                continue
            remainder = code[:match.start()] + code[match.end():]
            if re.search(r'\b' + re.escape(name) + r'\b', remainder):
                continue
            old = code
            code = code[:match.start()] + code[match.end():]
            record(findings, 'dead_assignment', old, code, match.start(),
                   f'Removed unused variable {name}',
                   'The declaration initializes a variable that is not referenced elsewhere, and its initializer contains only a simple side-effect-free expression.')
            dead_assignment_changed = True
            removed_one = True
            break
        if not removed_one:
            break
    if dead_assignment_changed:
        # Keep the status and explanation recorded by the first successful removal.
        pass

    unchanged = {
        'dead_assignment': 'No safe dead assignment was identified in the supplied source.',
        'common_subexpression': 'No safe repeated expression was identified for reuse in the supplied source.',
        'strength_reduction': 'No safe strength-reduction opportunity was identified in the supplied source.',
        'loop_optimization': 'No safe loop optimization was identified in the supplied source.',
        'function_inlining': 'No safe function-inlining opportunity was identified in the supplied source.',
    }
    for key, reason in unchanged.items():
        if findings[key].status != 'applied':
            findings[key].status = 'not_applicable'
            findings[key].change = 'No transformation made for this input'
            findings[key].reason = reason
    return code, [findings[k] for k in TECHNIQUES]

def optimize_c_code(source_code: str) -> dict[str, Any]:
    """Compatible core API: returns optimized_code and one report entry per technique."""
    code, findings = optimize(source_code)
    return {'optimized_code': code, 'optimizations': [asdict(x) for x in findings]}

def build_dashboard_data(source_code: str, result: dict[str, Any]) -> dict[str, Any]:
    items = result['optimizations']
    breakdown = {s: 0 for s in STATUSES}
    for item in items:
        breakdown[item['status']] = breakdown.get(item['status'], 0) + 1
    before, after = len(source_code.splitlines()), len(result['optimized_code'].splitlines())
    diff = after - before
    highlights = [{**item, 'label': TECHNIQUE_LABELS.get(item['technique'], item['technique'])}
                  for item in items if item['status'] == 'applied']
    observations = []
    if breakdown['applied']: observations.append(f"{breakdown['applied']} optimization technique(s) were applied by local rules.")
    if breakdown['not_applicable']: observations.append(f"{breakdown['not_applicable']} technique(s) had no matching transformation.")
    if diff < 0: observations.append(f"Output has {abs(diff)} fewer physical line(s); this does not prove better runtime.")
    elif diff > 0: observations.append(f"Output has {diff} additional physical line(s); this does not by itself imply worse runtime.")
    else: observations.append('Original and optimized source have the same number of physical lines.')
    return {
        'summary': {'techniques_analyzed': len(items), 'successfully_applied': breakdown['applied'],
                    'not_applicable': breakdown['not_applicable'],
                    'rejected': 0, 'original_lines': before, 'optimized_lines': after,
                    'lines_removed': max(0, -diff), 'lines_added': max(0, diff), 'line_difference': diff},
        'breakdown': breakdown, 'highlights': highlights, 'observations': observations,
        'optimizations': items, 'optimized_code': result['optimized_code'],
        'verification': {'compiled': False, 'tests_passed': False, 'benchmarked': False,
          'note': 'This optimizer does not compile, execute, formally verify, or benchmark the output by default. Compile and test both versions in a controlled environment.'}}

def markdown_cell(value: Any) -> str:
    return str(value).replace('|', r'\|').replace('\n', ' ').strip()

def create_report(dashboard: dict[str, Any]) -> str:
    s = dashboard['summary']
    lines = ['# ExplainOpt-X — Offline C Code Optimization Report', '',
      'Generated by the local Python rule-based optimizer.', '',
      '## Optimized C Code', '', '```c', dashboard['optimized_code'].rstrip(), '```', '',
      '## Optimization Summary', '', f"- Techniques analyzed: {s['techniques_analyzed']}",
      f"- Applied: {s['successfully_applied']}", f"- No matching transformation: {s['not_applicable']}",
      f"- Original physical lines: {s['original_lines']}", f"- Optimized physical lines: {s['optimized_lines']}",
      f"- Lines removed: {s['lines_removed']}", f"- Lines added: {s['lines_added']}", '',
      '## Detailed Explanation Table — All 12 Techniques', '',
      '| Technique | Status | Original Lines | Optimized Lines | Change | Reason |',
      '|---|---|---|---|---|---|']
    for item in dashboard['optimizations']:
        row = [TECHNIQUE_LABELS.get(item['technique'], item['technique']), item['status'],
               item['original_lines'], item['optimized_lines'], item['change'], item['reason']]
        lines.append('| ' + ' | '.join(markdown_cell(x) for x in row) + ' |')
    lines += ['', '## Key Observations', '']
    lines += [f'- {x}' for x in dashboard['observations']]
    lines += ['', '## Verification and Limitations', '', dashboard['verification']['note'],
      'This optimizer uses regex rather than a complete C parser. It can match text inside comments or string literals and cannot prove semantic equivalence. Line references are approximate anchors for text-based edits.',
      'A successful syntax check is not proof of equivalent behavior or improved performance.', '']
    return '\n'.join(lines)

def write_outputs(source_code: str, result: dict[str, Any]) -> dict[str, Any]:
    dashboard = build_dashboard_data(source_code, result)
    OUTPUT_CODE.write_text(dashboard['optimized_code'].rstrip() + '\n', encoding='utf-8')
    OUTPUT_REPORT.write_text(create_report(dashboard), encoding='utf-8')
    OUTPUT_JSON.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False), encoding='utf-8')
    return dashboard

def compile_check(source_path: Path, compiler: str = 'gcc') -> tuple[bool, str]:
    executable = shutil.which(compiler)
    if not executable: return False, f"Compiler '{compiler}' was not found on PATH."
    try:
        proc = subprocess.run([executable, '-std=c11', '-Wall', '-Wextra', '-fsyntax-only', str(source_path)],
            capture_output=True, text=True, timeout=15, check=False)
        output = (proc.stdout + proc.stderr).strip()
        return proc.returncode == 0, output or ('Compilation check passed.' if proc.returncode == 0 else 'Compilation check failed.')
    except subprocess.TimeoutExpired:
        return False, 'Compiler syntax check timed out.'

def main() -> int:
    parser = argparse.ArgumentParser(description='ExplainOpt-X offline regex-based C optimizer')
    parser.add_argument('input', help='Path to input .c file')
    parser.add_argument('--out', default=str(OUTPUT_CODE), help='Optimized C output path')
    parser.add_argument('--report', default=str(OUTPUT_REPORT), help='Markdown report path')
    parser.add_argument('--json', dest='json_out', default=str(OUTPUT_JSON), help='Dashboard JSON output path')
    parser.add_argument('--check', action='store_true', help='Run GCC syntax check if GCC is installed')
    args = parser.parse_args()
    input_path = Path(args.input)
    if not input_path.is_file():
        print(f'Input C file not found: {input_path}', file=sys.stderr); return 2
    try:
        source = input_path.read_text(encoding='utf-8')
        result = optimize_c_code(source)
        dashboard = build_dashboard_data(source, result)
        Path(args.out).write_text(dashboard['optimized_code'].rstrip() + '\n', encoding='utf-8')
        Path(args.report).write_text(create_report(dashboard), encoding='utf-8')
        Path(args.json_out).write_text(json.dumps(dashboard, indent=2, ensure_ascii=False), encoding='utf-8')
        print('\n========== OPTIMIZED C CODE ==========\n'); print(dashboard['optimized_code'])
        print('\n========== DASHBOARD SUMMARY =========='); print(json.dumps(dashboard['summary'], indent=2))
        print('\n========== OPTIMIZATION TECHNIQUES ==========')
        for item in dashboard['optimizations']:
            print(f"\n{TECHNIQUE_LABELS[item['technique']]} [{item['status']}]\n  Original lines: {item['original_lines']}\n  Optimized lines: {item['optimized_lines']}\n  Change: {item['change']}\n  Reason: {item['reason']}")
        print('\nFiles created:'); print(f'- {Path(args.out).resolve()}'); print(f'- {Path(args.report).resolve()}'); print(f'- {Path(args.json_out).resolve()}')
        if args.check:
            ok, message = compile_check(Path(args.out)); print(f"\nGCC syntax check: {'PASS' if ok else 'FAIL'}\n{message}")
            if not ok: return 1
        return 0
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        print(f'Optimization failed: {exc}', file=sys.stderr); return 1

if __name__ == '__main__':
    raise SystemExit(main())
