import networkx as nx
import numpy as np
import pytest
from sklearn.ensemble import RandomForestRegressor

from src.explainopt.instruction_attribution import (
    explain_graph,
    feature_vector,
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
    graph.add_edge(
        "entry:i0", "entry:i1", edge_type="data_dependency"
    )
    return graph


def fitted_model():
    X = np.array([
        [1, 0, 0, 0, 0],
        [2, 1, 1, 0, 0],
        [3, 2, 1, 1, 1],
        [4, 3, 2, 1, 1],
        [5, 4, 2, 2, 0],
    ], dtype=float)
    y = np.array([-4.0, -3.5, -3.0, -2.5, -2.0])
    return RandomForestRegressor(
        n_estimators=20, min_samples_leaf=1, random_state=42
    ).fit(X, y)


def test_feature_vector_counts_typed_edges():
    graph = sample_graph()
    features = feature_vector(graph, "O2")

    assert features.tolist() == [[2, 1, 1, 0, 1]]


def test_explanation_has_one_score_per_node():
    result = explain_graph(sample_graph(), fitted_model(), "O0")

    assert result["node_count"] == 2
    assert len(result["instructions"]) == 2
    assert all(
        row["method"] == "single_node_ablation"
        for row in result["instructions"]
    )


def test_explanation_is_deterministic():
    graph = sample_graph()
    model = fitted_model()

    first = explain_graph(graph, model, "O2")
    second = explain_graph(graph, model, "O2")

    assert first == second


def test_scores_are_finite_when_masked_graph_remains():
    result = explain_graph(sample_graph(), fitted_model(), "O0")

    for row in result["instructions"]:
        assert row["masked_log_runtime_prediction"] is not None
        assert np.isfinite(row["attribution"])


def test_single_node_graph_has_no_masked_prediction():
    graph = nx.MultiDiGraph()
    graph.add_node(
        "entry:i0", function="f", block="entry", line_number=1,
        opcode="ret", text="ret void",
    )

    result = explain_graph(graph, fitted_model(), "O0")

    assert result["instructions"][0]["attribution"] is None
    assert result["instructions"][0]["masked_log_runtime_prediction"] is None
