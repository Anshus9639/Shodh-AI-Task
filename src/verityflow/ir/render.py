"""Human-readable rendering of internal IR conditions.

Used for corrections and diagnostic explanations only; routing never depends on
this text. Values are rendered exactly (``Fraction`` has no floating rounding).
"""

from __future__ import annotations

from verityflow.ir.model import (
    And,
    Cmp,
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


def render_expr(expr: Expr) -> str:
    if isinstance(expr, Cmp):
        unit = f" {expr.unit}" if expr.unit else ""
        return f"{expr.field} {expr.op} {_number(expr.value)}{unit}"
    if isinstance(expr, Eq):
        return f"{expr.field} == {_literal(expr.value)}"
    if isinstance(expr, Ne):
        return f"{expr.field} != {_literal(expr.value)}"
    if isinstance(expr, In):
        return f"{expr.field} in {{{', '.join(sorted(_literal(value) for value in expr.values))}}}"
    if isinstance(expr, NotIn):
        return (
            f"{expr.field} not in {{{', '.join(sorted(_literal(value) for value in expr.values))}}}"
        )
    if isinstance(expr, Missing):
        return f"{expr.field} is absent"
    if isinstance(expr, And):
        return "(" + " and ".join(render_expr(item) for item in expr.items) + ")"
    if isinstance(expr, Or):
        return "(" + " or ".join(render_expr(item) for item in expr.items) + ")"
    if isinstance(expr, NotNode):
        return f"not ({render_expr(expr.item)})"
    if isinstance(expr, NotTrue):
        return f"no earlier condition holds ({render_expr(expr.item)})"
    return "true" if expr.value else "false"


def render_clause(condition: Expr, destination: str) -> str:
    return f"{render_expr(condition)} -> {destination!r}"


def _number(value: object) -> str:
    text = str(value)
    return text


def _literal(value: str | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(value)


__all__ = ["render_clause", "render_expr"]
