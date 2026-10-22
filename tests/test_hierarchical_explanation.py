
import networkx as nx
import pytest

from src.explainopt.hierarchical_explanation import (
    build_hierarchical_explanation,
)


def sample_graph():
    graph = nx.MultiDiGraph()
    graph.add_node(
        "entry:i0", function="f", block="entry", index=0,
        line_number=1, opcode="add", text="%x = add i32 %a, 1",
    )
    graph.add_node(
        "entry:i1", function="f", block="entry", index=1,
        line_number=2, opcode="mul", text="%y = mul i32 %x, 2",
    )
    graph.add_node(
        "loop:i0", function="f", block="loop", index=0,
        line_number=5, opcode="sub", text="%z = sub i32 %y, 1",
    )
    return graph


def test_aggregates_instruction_scores_by_block_and_function():
    result = build_hierarchical_explanation(
        sample_graph(),
        {"entry:i0": 0.2, "entry:i1": 0.3, "loop:i0": -0.5},
    )

    assert result["coverage"]["complete"] is True
    assert result["coverage"]["scored_instruction_count"] == 3

    blocks = {
        item["block"]: item["score"]
        for item in result["basic_blocks"]
    }
    assert blocks["entry"] == pytest.approx(0.5)
    assert blocks["loop"] == pytest.approx(-0.5)

    assert result["functions"][0]["function"] == "f"
    assert result["functions"][0]["score"] == pytest.approx(0.0)


def test_reports_missing_scores_instead_of_treating_them_as_zero():
    result = build_hierarchical_explanation(
        sample_graph(),
        {"entry:i0": 0.2},
    )

    assert result["coverage"]["complete"] is False
    assert result["coverage"]["missing_score_node_ids"] == [
        "entry:i1", "loop:i0"
    ]
    assert len(result["instructions"]) == 1


def test_mean_aggregation():
    result = build_hierarchical_explanation(
        sample_graph(),
        {"entry:i0": 0.2, "entry:i1": 0.4, "loop:i0": 0.8},
        aggregation="mean",
    )

    entry = next(item for item in result["basic_blocks"]
                 if item["block"] == "entry")
    assert entry["score"] == pytest.approx(0.3)


def test_rejects_unknown_nodes_and_nonfinite_scores():
    graph = sample_graph()

    with pytest.raises(ValueError, match="unknown graph nodes"):
        build_hierarchical_explanation(graph, {"not-a-node": 1.0})

    with pytest.raises(ValueError, match="Non-finite"):
        build_hierarchical_explanation(graph, {"entry:i0": float("nan")})


def test_rejects_unsupported_aggregation():
    with pytest.raises(ValueError, match="aggregation"):
        build_hierarchical_explanation(
            sample_graph(), {}, aggregation="max"
        )
