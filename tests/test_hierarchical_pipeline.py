
import networkx as nx
import numpy as np
import pytest

from src.explainopt import hierarchical_pipeline as pipeline


def make_graph():
    graph = nx.MultiDiGraph()
    graph.add_node(
        "main:entry:i0",
        function="main",
        block="entry",
        index=0,
        line_number=1,
        opcode="add",
        text="%1 = add i32 1, 2",
    )
    graph.add_node(
        "main:entry:i1",
        function="main",
        block="entry",
        index=1,
        line_number=2,
        opcode="ret",
        text="ret i32 %1",
    )
    graph.add_edge(
        "main:entry:i0",
        "main:entry:i1",
        edge_type="data_dependency",
    )
    return graph


def fake_attribution(graph, model, level):
    return {
        "schema_version": 1,
        "optimization_level": level,
        "baseline_log_runtime_prediction": -2.0,
        "baseline_runtime_prediction_seconds": float(np.exp(-2.0)),
        "node_count": 2,
        "attribution_method": "single_node_ablation",
        "score_definition": "Test perturbation score.",
        "limitations": ["Test limitation."],
        "instructions": [
            {"node_id": "main:entry:i0", "attribution": 0.25},
            {"node_id": "main:entry:i1", "attribution": -0.1},
        ],
    }


def setup_pipeline(monkeypatch, tmp_path):
    ir_file = tmp_path / "sample.ll"
    ir_file.write_text("; test IR\n", encoding="utf-8")

    monkeypatch.setattr(
        pipeline, "load_training_data",
        lambda: (np.array([[1, 2, 1, 0, 0]] * 4), np.array([-2., -2., -2., -2.])),
    )
    monkeypatch.setattr(pipeline, "graph_for_ir", lambda path: make_graph())
    monkeypatch.setattr(pipeline, "explain_graph", fake_attribution)

    return ir_file


def test_pipeline_aggregates_instruction_scores(monkeypatch, tmp_path):
    ir_file = setup_pipeline(monkeypatch, tmp_path)

    report = pipeline.generate_hierarchical_report(ir_file, "O0")

    hierarchy = report["hierarchy"]
    assert hierarchy["coverage"]["complete"] is True
    assert hierarchy["coverage"]["graph_node_count"] == 2
    assert hierarchy["coverage"]["scored_instruction_count"] == 2

    assert len(hierarchy["basic_blocks"]) == 1
    assert hierarchy["basic_blocks"][0]["score"] == pytest.approx(0.15)

    assert len(hierarchy["functions"]) == 1
    assert hierarchy["functions"][0]["function"] == "main"
    assert hierarchy["functions"][0]["score"] == pytest.approx(0.15)


def test_pipeline_rejects_invalid_optimization_level():
    with pytest.raises(ValueError, match="optimization_level"):
        pipeline.generate_hierarchical_report("unused.ll", "O1")


def test_pipeline_rejects_missing_ir_file(tmp_path):
    missing = tmp_path / "missing.ll"

    with pytest.raises(FileNotFoundError):
        pipeline.generate_hierarchical_report(missing, "O0")
