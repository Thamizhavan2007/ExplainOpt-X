
import json

import pytest

from src.explainopt.counterfactual.experiment_runner import run_experiment


def test_missing_input_file_raises_error(tmp_path):
    missing_file = tmp_path / "missing.ll"

    with pytest.raises(FileNotFoundError):
        run_experiment(
            input_path=missing_file,
            repeats=5,
            output_path=tmp_path / "report.json",
            clang_path="clang",
        )


def test_too_few_repetitions_raises_error(tmp_path):
    input_file = tmp_path / "input.ll"
    input_file.write_text(
        "define i32 @main() {\nentry:\n  ret i32 0\n}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="at least 5"):
        run_experiment(
            input_path=input_file,
            repeats=4,
            output_path=tmp_path / "report.json",
            clang_path="clang",
        )


def test_unsupported_transformation_reports_no_candidate(tmp_path):
    input_file = tmp_path / "input.ll"
    input_file.write_text(
        "define i32 @main() {\nentry:\n  ret i32 0\n}\n",
        encoding="utf-8",
    )

    report_path = tmp_path / "report.json"

    report = run_experiment(
        input_path=input_file,
        repeats=5,
        output_path=report_path,
        clang_path="clang",
    )

    assert report["status"] == "no_candidate"
    assert report["transformation_applied"] is False
    assert report["formal_equivalence_proven"] is False

    saved_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved_report["status"] == "no_candidate"


def test_report_marks_formal_equivalence_unproven(tmp_path):
    input_file = tmp_path / "input.ll"
    input_file.write_text(
        "define i32 @main() {\nentry:\n  ret i32 0\n}\n",
        encoding="utf-8",
    )

    report = run_experiment(
        input_path=input_file,
        repeats=5,
        output_path=tmp_path / "report.json",
        clang_path="clang",
    )

    assert report["formal_equivalence_proven"] is False
