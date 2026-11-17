from __future__ import annotations

from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from verityflow.ir.evaluate import (
    CellValue,
    EvaluationError,
    Truth,
    ValueState,
    evaluate,
)
from verityflow.ir.model import (
    And,
    Cmp,
    Const,
    Eq,
    In,
    Missing,
    Ne,
    NotIn,
    NotNode,
    NotTrue,
    Or,
)

UNKNOWN = Cmp(field="wait", op=">=", value=Fraction(30), unit="minutes")
TRUE = Const(value=True)
FALSE = Const(value=False)

_EXPRESSIONS = {Truth.TRUE: TRUE, Truth.FALSE: FALSE, Truth.UNKNOWN: UNKNOWN}

_VALUES = {
    "category": CellValue.present("billing"),
    "wait": CellValue.present(Fraction(30)),
    "vip": CellValue.present(True),
}


def test_missing_comparison_is_unknown() -> None:
    assert evaluate(Cmp(field="wait", op=">=", value=30, unit="minutes"), {}) is Truth.UNKNOWN
    assert (
        evaluate(Cmp(field="wait", op=">=", value=30, unit="minutes"), {"wait": CellValue.null()})
        is Truth.UNKNOWN
    )


def test_missing_is_total() -> None:
    expr = Missing(field="category")
    assert evaluate(expr, {}) is Truth.TRUE
    assert evaluate(expr, {"category": CellValue.null()}) is Truth.TRUE
    assert evaluate(expr, {"category": CellValue.absent()}) is Truth.TRUE
    assert evaluate(expr, _VALUES) is Truth.FALSE


def test_equality_and_membership_are_unknown_on_absent_values() -> None:
    assert evaluate(Eq(field="category", value="billing"), {}) is Truth.UNKNOWN
    assert evaluate(Ne(field="category", value="billing"), {}) is Truth.UNKNOWN
    assert evaluate(In(field="category", values=frozenset({"billing"})), {}) is Truth.UNKNOWN
    assert evaluate(NotIn(field="category", values=frozenset({"billing"})), {}) is Truth.UNKNOWN
    assert evaluate(Eq(field="category", value="billing"), _VALUES) is Truth.TRUE
    assert evaluate(Ne(field="category", value="billing"), _VALUES) is Truth.FALSE
    assert evaluate(In(field="category", values=frozenset({"billing"})), _VALUES) is Truth.TRUE
    assert evaluate(NotIn(field="category", values=frozenset({"billing"})), _VALUES) is Truth.FALSE


def test_kleene_and_table() -> None:
    expected = {
        (Truth.TRUE, Truth.TRUE): Truth.TRUE,
        (Truth.TRUE, Truth.FALSE): Truth.FALSE,
        (Truth.TRUE, Truth.UNKNOWN): Truth.UNKNOWN,
        (Truth.FALSE, Truth.TRUE): Truth.FALSE,
        (Truth.FALSE, Truth.FALSE): Truth.FALSE,
        (Truth.FALSE, Truth.UNKNOWN): Truth.FALSE,
        (Truth.UNKNOWN, Truth.TRUE): Truth.UNKNOWN,
        (Truth.UNKNOWN, Truth.FALSE): Truth.FALSE,
        (Truth.UNKNOWN, Truth.UNKNOWN): Truth.UNKNOWN,
    }
    for (left, right), result in expected.items():
        expr = And(items=(_EXPRESSIONS[left], _EXPRESSIONS[right]))
        assert evaluate(expr, {}) is result


def test_kleene_or_table() -> None:
    expected = {
        (Truth.TRUE, Truth.TRUE): Truth.TRUE,
        (Truth.TRUE, Truth.FALSE): Truth.TRUE,
        (Truth.TRUE, Truth.UNKNOWN): Truth.TRUE,
        (Truth.FALSE, Truth.TRUE): Truth.TRUE,
        (Truth.FALSE, Truth.FALSE): Truth.FALSE,
        (Truth.FALSE, Truth.UNKNOWN): Truth.UNKNOWN,
        (Truth.UNKNOWN, Truth.TRUE): Truth.TRUE,
        (Truth.UNKNOWN, Truth.FALSE): Truth.UNKNOWN,
        (Truth.UNKNOWN, Truth.UNKNOWN): Truth.UNKNOWN,
    }
    for (left, right), result in expected.items():
        expr = Or(items=(_EXPRESSIONS[left], _EXPRESSIONS[right]))
        assert evaluate(expr, {}) is result


def test_kleene_not() -> None:
    assert evaluate(NotNode(item=TRUE), {}) is Truth.FALSE
    assert evaluate(NotNode(item=FALSE), {}) is Truth.TRUE
    assert evaluate(NotNode(item=UNKNOWN), {}) is Truth.UNKNOWN


def test_not_true_blocks_only_true() -> None:
    assert evaluate(NotTrue(item=TRUE), {}) is Truth.FALSE
    assert evaluate(NotTrue(item=FALSE), {}) is Truth.TRUE
    assert evaluate(NotTrue(item=UNKNOWN), {}) is Truth.TRUE


def test_and_short_circuits_on_false() -> None:
    # The second item would raise a type error if it were evaluated.
    expr = And(
        items=(
            FALSE,
            Cmp(field="category", op=">=", value=1, unit="minutes"),
        )
    )
    assert evaluate(expr, _VALUES) is Truth.FALSE


def test_type_errors_are_explicit() -> None:
    with pytest.raises(EvaluationError):
        evaluate(Cmp(field="category", op=">=", value=1, unit="minutes"), _VALUES)
    with pytest.raises(EvaluationError):
        evaluate(Eq(field="wait", value="billing"), _VALUES)
    with pytest.raises(EvaluationError):
        evaluate(In(field="wait", values=frozenset({"billing"})), _VALUES)


@given(left=st.sampled_from(list(Truth)), right=st.sampled_from(list(Truth)))
def test_kleene_properties(left: Truth, right: Truth) -> None:
    negated = {
        Truth.TRUE: Truth.FALSE,
        Truth.FALSE: Truth.TRUE,
        Truth.UNKNOWN: Truth.UNKNOWN,
    }
    assert evaluate(NotNode(item=_EXPRESSIONS[left]), {}) is negated[left]
    assert evaluate(NotTrue(item=_EXPRESSIONS[left]), {}) is (
        Truth.FALSE if left is Truth.TRUE else Truth.TRUE
    )
    conjunction = And(items=(_EXPRESSIONS[left], _EXPRESSIONS[right]))
    disjunction = Or(items=(_EXPRESSIONS[left], _EXPRESSIONS[right]))
    if Truth.FALSE in (left, right):
        assert evaluate(conjunction, {}) is Truth.FALSE
    if Truth.TRUE in (left, right):
        assert evaluate(disjunction, {}) is Truth.TRUE


def test_value_states_are_distinct() -> None:
    assert CellValue.absent().state is ValueState.ABSENT
    assert CellValue.null().state is ValueState.NULL
    assert CellValue.present("x").state is ValueState.PRESENT
