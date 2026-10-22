
import copy

import pytest

from src.explainopt.explanation_audit import audit_report


@pytest.fixture
def valid_report():
    return {
        "attribution": {
            "instructions": [
                {
                    "node_id": "main:entry:i0",
                    "function": "main",
                    "block": "entry",
                    "attribution": 0.25,
                },
                {
                    "node_id": "main:entry:i1",
                    "function": "main",
                    "block": "entry",
                    "attribution": -0.10,
                },
            ],
        },
        "hierarchy": {
            "aggregation": "sum",
            "coverage": {
                "graph_node_count": 2,
                "scored_instruction_count": 2,
                "complete": True,
            },
            "basic_blocks": [
                {
                    "function": "main",
                    "block": "entry",
                    "score": 0.15,
                }
            ],
            "functions": [
                {
                    "function": "main",
                    "score": 0.15,
                }
            ],
        },
        "limitations": ["Scores are not causal effects."],
    }


def check(report, name):
    return next(item for item in report["checks"] if item["name"] == name)


def test_valid_report_passes_integrity_checks(valid_report):
    result = audit_report(valid_report)

    assert check(result, "finite_instruction_scores")["passed"]
    assert check(result, "instruction_coverage")["passed"]
    assert check(result, "unique_instruction_ids")["passed"]
    assert check(result, "block_aggregation_consistency")["passed"]
    assert check(result, "function_aggregation_consistency")["passed"]


def test_incomplete_coverage_is_flagged(valid_report):
    valid_report["hierarchy"]["coverage"]["complete"] = False

    result = audit_report(valid_report)

    assert not check(result, "instruction_coverage")["passed"]
    assert result["audit_status"] == "warning"


def test_duplicate_instruction_ids_are_flagged(valid_report):
    valid_report["attribution"]["instructions"][1]["node_id"] = "main:entry:i0"

    result = audit_report(valid_report)

    assert not check(result, "unique_instruction_ids")["passed"]


def test_repeated_scores_are_flagged(valid_report):
    valid_report["attribution"]["instructions"][1]["attribution"] = 0.25
    valid_report["hierarchy"]["basic_blocks"][0]["score"] = 0.50
    valid_report["hierarchy"]["functions"][0]["score"] = 0.50

    result = audit_report(valid_report)

    assert not check(result, "score_repetition")["passed"]
    assert result["audit_status"] == "warning"


def test_incorrect_block_aggregate_is_flagged(valid_report):
    valid_report["hierarchy"]["basic_blocks"][0]["score"] = 99.0

    result = audit_report(valid_report)

    assert not check(result, "block_aggregation_consistency")["passed"]


def test_incorrect_function_aggregate_is_flagged(valid_report):
    valid_report["hierarchy"]["functions"][0]["score"] = -99.0

    result = audit_report(valid_report)

    assert not check(result, "function_aggregation_consistency")["passed"]


def test_non_finite_score_is_flagged(valid_report):
    valid_report["attribution"]["instructions"][0]["attribution"] = float("nan")

    result = audit_report(valid_report)

    assert not check(result, "finite_instruction_scores")["passed"]


def test_mean_aggregation_is_checked(valid_report):
    report = copy.deepcopy(valid_report)
    report["hierarchy"]["aggregation"] = "mean"
    report["hierarchy"]["basic_blocks"][0]["score"] = 0.075
    report["hierarchy"]["functions"][0]["score"] = 0.075

    result = audit_report(report)

    assert check(result, "block_aggregation_consistency")["passed"]
    assert check(result, "function_aggregation_consistency")["passed"]
