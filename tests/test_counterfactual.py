
from src.explainopt.counterfactual.engine import (
    simplify_add_zero,
    validate_candidate,
)


IDENTITY_IR = """\
define i32 @identity_add(i32 %x) {
entry:
  %result = add i32 %x, 0
  ret i32 %result
}
"""


def test_add_zero_rewrite_replaces_uses():
    candidate, changes = simplify_add_zero(IDENTITY_IR)

    assert len(changes) == 1
    assert changes[0].applied is True
    assert "add i32" not in candidate
    assert "ret i32 %x" in candidate


def test_add_zero_candidate_compiles():
    candidate, changes = simplify_add_zero(IDENTITY_IR)

    assert changes
    assert changes[0].applied is True

    structural_ok, compile_ok, error = validate_candidate(candidate)

    assert structural_ok, error
    assert compile_ok, error


def test_unrelated_ir_is_unchanged():
    original = """\
define i32 @sum(i32 %x, i32 %y) {
entry:
  %result = add i32 %x, %y
  ret i32 %result
}
"""

    candidate, changes = simplify_add_zero(original)

    assert candidate == original
    assert changes == []
