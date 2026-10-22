
from pathlib import Path

from src.explainopt.cfg import build_cfg, build_module_cfgs
from src.explainopt.ir_parser import parse_ir


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IR_DIR = PROJECT_ROOT / "samples" / "source"


def test_unoptimized_cfg_structure():
    module = parse_ir(IR_DIR / "sum.ll")
    results = build_module_cfgs(module)

    assert len(results) == 1

    cfg = results[0]
    assert cfg.function_name == "sum"
    assert cfg.graph.number_of_nodes() == 5
    assert cfg.graph.number_of_edges() == 5


def test_cfg_contains_loop_back_edge():
    module = parse_ir(IR_DIR / "sum.ll")
    cfg = build_cfg(module.functions[0])

    # The original loop returns from block 19 to block 7.
    assert cfg.graph.has_edge("19", "7")


def test_optimized_cfg_has_vector_instructions():
    module = parse_ir(IR_DIR / "sum_O2.ll")
    cfg = build_cfg(module.functions[0])

    opcodes = {
        opcode
        for _, attributes in cfg.graph.nodes(data=True)
        for opcode in attributes["opcodes"]
    }

    assert "phi" in opcodes
    assert "load" in opcodes
    assert cfg.graph.number_of_nodes() > 0


def test_cfg_serialization():
    module = parse_ir(IR_DIR / "sum.ll")
    cfg = build_cfg(module.functions[0])
    data = cfg.to_dict()

    assert data["function"] == "sum"
    assert len(data["nodes"]) == 5
    assert len(data["edges"]) == 5
