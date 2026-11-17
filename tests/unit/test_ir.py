from __future__ import annotations

from fractions import Fraction

import pytest
from pydantic import TypeAdapter, ValidationError

from verityflow.ir.model import (
    And,
    Cmp,
    Const,
    Eq,
    Expr,
    In,
    Missing,
    Ne,
    NotIn,
    NotNode,
    NotTrue,
    Or,
    canonical_key,
    referenced_fields,
)

ADAPTER = TypeAdapter(Expr)


def _round_trip(expr: Expr) -> Expr:
    text = ADAPTER.dump_json(expr)
    return ADAPTER.validate_json(text)


@pytest.mark.parametrize(
    "expr",
    [
        Cmp(field="wait", op=">=", value=Fraction(30), unit="minutes"),
        Eq(field="category", value="billing"),
        Ne(field="vip", value=True),
        In(field="category", values=frozenset({"billing", "technical"})),
        NotIn(field="category", values=frozenset({"billing"})),
        Missing(field="category"),
        And(items=(Eq(field="vip", value=True), Missing(field="category"))),
        Or(items=(Eq(field="vip", value=True), Missing(field="category"))),
        NotNode(item=Eq(field="vip", value=True)),
        NotTrue(item=Eq(field="vip", value=True)),
        Const(value=True),
    ],
)
def test_ir_round_trips_through_json(expr: Expr) -> None:
    assert _round_trip(expr) == expr


def test_not_true_discriminator_is_distinct_from_not() -> None:
    payload = NotTrue(item=Const(value=True)).model_dump()
    assert payload["kind"] == "not_true"
    assert NotNode(item=Const(value=True)).model_dump()["kind"] == "not"


def test_numeric_literals_are_exact() -> None:
    assert Cmp(field="x", op=">", value=0.1).value == Fraction(1, 10)
    assert Cmp(field="x", op=">", value="0.1").value == Fraction(1, 10)
    assert Cmp(field="x", op=">", value=3).value == Fraction(3)


def test_boolean_is_not_a_numeric_literal() -> None:
    with pytest.raises(ValidationError):
        Cmp(field="x", op=">", value=True)


def test_nodes_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Cmp(field="x", op=">", value=1, extra="nope")  # type: ignore[call-arg]


def test_referenced_fields_walk_guards() -> None:
    expr = And(
        items=(
            Eq(field="priority", value="urgent"),
            NotTrue(
                item=And(
                    items=(
                        Eq(field="category", value="billing"),
                        Cmp(field="wait", op=">=", value=30, unit="minutes"),
                    )
                )
            ),
        )
    )
    assert referenced_fields(expr) == frozenset({"priority", "category", "wait"})
    assert referenced_fields(Const(value=True)) == frozenset()


def test_canonical_key_marks_guards() -> None:
    inner = Eq(field="priority", value="urgent")
    assert canonical_key(NotTrue(item=inner)) == f"not_true({canonical_key(inner)})"
    assert canonical_key(NotTrue(item=inner)) != canonical_key(NotNode(item=inner))


def test_canonical_key_ignores_item_order() -> None:
    first = Eq(field="category", value="billing")
    second = Eq(field="priority", value="urgent")
    assert canonical_key(And(items=(first, second))) == canonical_key(And(items=(second, first)))
