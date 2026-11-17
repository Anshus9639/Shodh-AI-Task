from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

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
)


class Truth(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class ValueState(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    NULL = "null"


@dataclass(frozen=True)
class CellValue:
    state: ValueState
    value: Fraction | str | bool | None = None

    @classmethod
    def present(cls, value: Fraction | str | bool) -> CellValue:
        return cls(ValueState.PRESENT, value)

    @classmethod
    def absent(cls) -> CellValue:
        return cls(ValueState.ABSENT)

    @classmethod
    def null(cls) -> CellValue:
        return cls(ValueState.NULL)


class EvaluationError(ValueError):
    pass


_ABSENT = CellValue.absent()
_NULL = CellValue.null()


def _cell_value(values: Mapping[str, CellValue], field: str) -> CellValue:
    return values.get(field, _ABSENT)


def evaluate(expr: Expr, values: Mapping[str, CellValue]) -> Truth:
    if isinstance(expr, Cmp):
        cell = _cell_value(values, expr.field)
        if cell.state is not ValueState.PRESENT:
            return Truth.UNKNOWN
        if not isinstance(cell.value, Fraction):
            raise EvaluationError(
                f"field {expr.field!r} holds a non-numeric value for a numeric comparison"
            )
        value = cell.value
        if expr.op == "<":
            return _truth(value < expr.value)
        if expr.op == "<=":
            return _truth(value <= expr.value)
        if expr.op == ">":
            return _truth(value > expr.value)
        if expr.op == ">=":
            return _truth(value >= expr.value)
        if expr.op == "==":
            return _truth(value == expr.value)
        return _truth(value != expr.value)
    if isinstance(expr, (Eq, Ne)):
        cell = _cell_value(values, expr.field)
        if cell.state is not ValueState.PRESENT:
            return Truth.UNKNOWN
        if isinstance(cell.value, Fraction):
            raise EvaluationError(
                f"field {expr.field!r} holds a numeric value for a categorical comparison"
            )
        equal = cell.value == expr.value
        return _truth(equal if isinstance(expr, Eq) else not equal)
    if isinstance(expr, (In, NotIn)):
        cell = _cell_value(values, expr.field)
        if cell.state is not ValueState.PRESENT:
            return Truth.UNKNOWN
        if isinstance(cell.value, Fraction):
            raise EvaluationError(
                f"field {expr.field!r} holds a numeric value for a membership test"
            )
        member = cell.value in expr.values
        return _truth(member if isinstance(expr, In) else not member)
    if isinstance(expr, Missing):
        cell = _cell_value(values, expr.field)
        return _truth(cell.state is ValueState.ABSENT or cell.state is ValueState.NULL)
    if isinstance(expr, And):
        saw_unknown = False
        for item in expr.items:
            result = evaluate(item, values)
            if result is Truth.FALSE:
                return Truth.FALSE
            if result is Truth.UNKNOWN:
                saw_unknown = True
        return Truth.UNKNOWN if saw_unknown else Truth.TRUE
    if isinstance(expr, Or):
        saw_unknown = False
        for item in expr.items:
            result = evaluate(item, values)
            if result is Truth.TRUE:
                return Truth.TRUE
            if result is Truth.UNKNOWN:
                saw_unknown = True
        return Truth.UNKNOWN if saw_unknown else Truth.FALSE
    if isinstance(expr, NotNode):
        inner = evaluate(expr.item, values)
        if inner is Truth.TRUE:
            return Truth.FALSE
        if inner is Truth.FALSE:
            return Truth.TRUE
        return Truth.UNKNOWN
    if isinstance(expr, NotTrue):
        inner = evaluate(expr.item, values)
        return Truth.FALSE if inner is Truth.TRUE else Truth.TRUE
    if isinstance(expr, Const):
        return Truth.TRUE if expr.value else Truth.FALSE
    raise EvaluationError(f"unsupported expression node: {type(expr).__name__}")


def _truth(value: bool) -> Truth:
    return Truth.TRUE if value else Truth.FALSE
