
from pathlib import Path

from src.explainopt.ir_parser import parse_ir
from src.explainopt.program_graph import build_program_graph, graph_summary


ROOT = Path(__file__).resolve().parents[1]
IR_DIR = ROOT / "samples" / "source"


def test_graph_has_instruction_nodes():
    module = parse_ir(IR_DIR / "sum.ll")
    graph = build_program_graph(module.functions[0])

    assert graph.number_of_nodes() == 28
    assert all("opcode" in data for _, data in graph.nodes(data=True))


def test_graph_has_data_dependencies():
    module = parse_ir(IR_DIR / "sum.ll")
    graph = build_program_graph(module.functions[0])

    edges = [
        (u, v)
        for u, v, data in graph.edges(data=True)
        if data["edge_type"] == "data_dependency"
    ]
    assert edges


def test_graph_has_control_flow_edges():
    module = parse_ir(IR_DIR / "sum.ll")
    graph = build_program_graph(module.functions[0])

    edges = [
        (u, v)
        for u, v, data in graph.edges(data=True)
        if data["edge_type"] == "control_flow"
    ]
    assert len(edges) == 5


def test_graph_summary():
    module = parse_ir(IR_DIR / "sum.ll")
    summary = graph_summary(build_program_graph(module.functions[0]))

    assert summary["instruction_nodes"] == 28
    assert summary["total_edges"] > 0
    assert summary["edge_counts"]["data_dependency"] > 0
    assert summary["edge_counts"]["control_flow"] == 5