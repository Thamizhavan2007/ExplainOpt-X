import json
from pathlib import Path

from src.explainopt.counterfactual.benchmark import main


def test_benchmark_generates_valid_report(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    main(repeats=5)

    report_path = Path("results/counterfactual/benchmark_report.json")
    assert report_path.exists()

    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["candidate_structural_validation_passed"] is True
    assert report["candidate_compilation_passed"] is True
    assert report["differential_behavior_check_passed"] is True
    assert report["repeated_output_and_exit_status_consistent"] is True
    assert report["formal_equivalence_proven"] is False

    assert report["repetitions_per_version"] == 5
    assert report["original_measurement"]["repetitions"] == 5
    assert report["candidate_measurement"]["repetitions"] == 5

    assert report["original_measurement"]["median_seconds"] > 0
    assert report["candidate_measurement"]["median_seconds"] > 0
    assert report["speedup_original_over_candidate"] > 0
