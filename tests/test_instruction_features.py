
import networkx as nx
import pytest

from src.explainopt.instruction_features import (
    extract_instruction_features,
    summarize_instruction_features,
)


def make_graph():
    graph = nx.MultiDiGraph()
    graph.add_node(
        "entry:i0", function="f", block="entry", index=0,
        line_number=1, opcode="add", text="%x = add i32 %a, 1",
    )
    graph.add_node(
        "entry:i1", function="f", block="entry", index=1,
        line_number=2, opcode="mul", text="%y = mul i32 %x, 2",
    )
    graph.add_edge(
        "entry:i0", "entry:i1", edge_type="data_dependency"
    )
    return graph


def test_extracts_instruction_features_and_degrees():
    records = extract_instruction_features(make_graph())
    by_id = {record["node_id"]: record for record in records}

    assert len(records) == 2
    assert by_id["entry:i0"]["out_data_degree"] == 1
    assert by_id["entry:i1"]["in_data_degree"] == 1
    assert by_id["entry:i1"]["in_control_degree"] == 0


def test_summary_counts_opcodes():
    records = extract_instruction_features(make_graph())
    summary = summarize_instruction_features(records)

    assert summary["instruction_count"] == 2
    assert summary["opcode_counts"] == {"add": 1, "mul": 1}
    assert "not learned attribution scores" in summary["interpretation"]


def test_empty_graph_returns_empty_summary():
    summary = summarize_instruction_features(
        extract_instruction_features(nx.MultiDiGraph())
    )

    assert summary["instruction_count"] == 0
    assert summary["opcode_counts"] == {}
