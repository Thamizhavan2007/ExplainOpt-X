
from src.explainopt.counterfactual.engine import simplify_add_zero, simplify_mul_one
from src.explainopt.counterfactual.behavior import compare_behavior
from src.explainopt.counterfactual.function_behavior import (
    compare_function_behavior,
)


IDENTITY_IR = """\
define i32 @main(i32 %arg) {
entry:
  %x = add i32 %arg, 0
  ret i32 %x
}
"""


IDENTITY_FUNCTION_IR = """\
define i32 @identity(i32 %arg) {
entry:
  %value = mul i32 %arg, 1
  ret i32 %value
}

define i32 @main() {
entry:
  ret i32 0
}
"""


def test_original_and_candidate_behave_identically():
    candidate, changes = simplify_add_zero(IDENTITY_IR)

    assert len(changes) == 1
    assert changes[0].applied

    report = compare_behavior(
        original_ir=IDENTITY_IR,
        candidate_ir=candidate,
    )

    assert report.passed
    assert report.formal_equivalence_proven is False


def test_identity_function_matches_on_boundary_and_regular_inputs():
    candidate, changes = simplify_mul_one(IDENTITY_FUNCTION_IR)

    assert any(change.applied for change in changes)

    report = compare_function_behavior(
        original_ir=IDENTITY_FUNCTION_IR,
        candidate_ir=candidate,
        function_name="identity",
    )

    assert report.passed, report.error
    assert report.original_results == report.candidate_results
    assert report.test_values == [
        0, 1, -1, 2, -2, 40, -40, 2**31 - 1, -(2**31)
    ]
    assert report.formal_equivalence_proven is False


def test_function_checker_rejects_missing_function():
    report = compare_function_behavior(
        original_ir=IDENTITY_FUNCTION_IR,
        candidate_ir=IDENTITY_FUNCTION_IR,
        function_name="function_that_does_not_exist",
        test_values=[0, 1],
    )

    assert not report.passed
    assert report.error is not None
    assert report.formal_equivalence_proven is False
