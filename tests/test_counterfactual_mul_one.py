
from src.explainopt.counterfactual.engine import (
    simplify_mul_one,
    validate_candidate,
)


MUL_ONE_IR = """\
define i32 @identity(i32 %arg) {
entry:
  %value = mul i32 %arg, 1
  ret i32 %value
}
"""


def test_mul_one_rewrite_replaces_uses():
    candidate, changes = simplify_mul_one(MUL_ONE_IR)

    assert len(changes) == 1
    assert changes[0].applied is True
    assert changes[0].transformation == "mul_one_identity"
    assert "%value = mul i32 %arg, 1" not in candidate
    assert "ret i32 %arg" in candidate


def test_mul_one_candidate_compiles():
    candidate, changes = simplify_mul_one(MUL_ONE_IR)

    assert changes
    structural_ok, llvm_ok, error = validate_candidate(candidate)

    assert structural_ok, error
    assert llvm_ok, error


def test_unrelated_ir_is_unchanged_by_mul_one():
    ir = """\
define i32 @identity(i32 %arg) {
entry:
  %value = add i32 %arg, 2
  ret i32 %value
}
"""

    candidate, changes = simplify_mul_one(ir)

    assert candidate == ir
    assert changes == []


def test_mul_one_does_not_change_add_zero():
    candidate, changes = simplify_mul_one(
        """\
define i32 @identity(i32 %arg) {
entry:
  %value = add i32 %arg, 0
  ret i32 %value
}
"""
    )

    assert candidate.endswith("}\n")
    assert "add i32 %arg, 0" in candidate
    assert changes == []

