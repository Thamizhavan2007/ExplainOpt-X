
from pathlib import Path

from src.explainopt.ir_parser import parse_ir, summarize_ir


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IR_DIR = PROJECT_ROOT / "samples" / "source"


def test_parse_unoptimized_sum_ir():
    module = parse_ir(IR_DIR / "sum.ll")

    assert len(module.functions) == 1
    assert module.functions[0].name == "sum"

    summary = summarize_ir(module)
    function = summary["functions"][0]

    assert function["basic_block_count"] == 5
    assert function["instruction_count"] == 28
    assert function["opcode_counts"]["load"] == 8
    assert function["opcode_counts"]["store"] == 6


def test_parse_optimized_sum_ir():
    module = parse_ir(IR_DIR / "sum_O2.ll")

    assert len(module.functions) == 1
    assert module.functions[0].name == "sum"

    summary = summarize_ir(module)
    function = summary["functions"][0]

    assert function["basic_block_count"] > 0
    assert function["instruction_count"] > 0
    assert "phi" in function["opcode_counts"]


def test_branch_targets_resolve():
    module = parse_ir(IR_DIR / "sum.ll")

    assert not any(
        "Unresolved branch target" in warning
        for warning in module.warnings
    )
