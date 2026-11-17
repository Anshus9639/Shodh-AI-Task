from __future__ import annotations

import json
from fractions import Fraction
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer


def _to_fraction(value: Any) -> Fraction:
    if isinstance(value, bool):
        raise ValueError("booleans are not numeric literals")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(repr(value))
    if isinstance(value, str):
        try:
            return Fraction(value.strip())
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError(f"not an exact number: {value!r}") from exc
    raise ValueError(f"not an exact number: {value!r}")


def _fraction_to_text(value: Fraction) -> str:
    return str(value)


NumericLit = Annotated[
    Fraction,
    BeforeValidator(_to_fraction),
    PlainSerializer(_fraction_to_text, return_type=str),
]


class IrNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Cmp(IrNode):
    kind: Literal["cmp"] = "cmp"
    field: str = Field(min_length=1)
    op: Literal["<", "<=", ">", ">=", "==", "!="]
    value: NumericLit
    unit: str | None = None


class Eq(IrNode):
    kind: Literal["eq"] = "eq"
    field: str = Field(min_length=1)
    value: str | bool


class Ne(IrNode):
    kind: Literal["ne"] = "ne"
    field: str = Field(min_length=1)
    value: str | bool


class In(IrNode):
    kind: Literal["in"] = "in"
    field: str = Field(min_length=1)
    values: frozenset[str | bool] = Field(min_length=1)


class NotIn(IrNode):
    kind: Literal["not_in"] = "not_in"
    field: str = Field(min_length=1)
    values: frozenset[str | bool] = Field(min_length=1)


class Missing(IrNode):
    kind: Literal["missing"] = "missing"
    field: str = Field(min_length=1)


class And(IrNode):
    kind: Literal["and"] = "and"
    items: tuple[Expr, ...] = Field(min_length=2)


class Or(IrNode):
    kind: Literal["or"] = "or"
    items: tuple[Expr, ...] = Field(min_length=2)


class NotNode(IrNode):
    kind: Literal["not"] = "not"
    item: Expr


class NotTrue(IrNode):
    """Internal first-match guard: TRUE unless the item is TRUE.

    Used to express "no earlier rule or branch fired". Unlike Kleene negation,
    UNKNOWN satisfies it, which matches ordered first-match semantics.
    """

    kind: Literal["not_true"] = "not_true"
    item: Expr


class Const(IrNode):
    kind: Literal["const"] = "const"
    value: bool


Expr: TypeAlias = Annotated[  # noqa: UP040 - Pydantic requires a runtime alias
    Cmp | Eq | Ne | In | NotIn | Missing | And | Or | NotNode | NotTrue | Const,
    Field(discriminator="kind"),
]

_TYPES_NAMESPACE = {"Expr": Expr}
for _model in (And, Or, NotNode, NotTrue):
    _model.model_rebuild(force=True, _types_namespace=_TYPES_NAMESPACE)


def referenced_fields(expr: Expr) -> frozenset[str]:
    if isinstance(expr, (Cmp, Eq, Ne, In, NotIn, Missing)):
        return frozenset({expr.field})
    if isinstance(expr, (And, Or)):
        result: set[str] = set()
        for item in expr.items:
            result.update(referenced_fields(item))
        return frozenset(result)
    if isinstance(expr, (NotNode, NotTrue)):
        return referenced_fields(expr.item)
    return frozenset()


def canonical_key(expr: Expr) -> str:
    """Stable structural key; used only for grouping and tie-breaks, never as proof."""
    if isinstance(expr, Cmp):
        return f"cmp|{expr.field}|{expr.op}|{expr.value}|{expr.unit or ''}"
    if isinstance(expr, Eq):
        return f"eq|{expr.field}|{_literal_key(expr.value)}"
    if isinstance(expr, Ne):
        return f"ne|{expr.field}|{_literal_key(expr.value)}"
    if isinstance(expr, In):
        return f"in|{expr.field}|{','.join(sorted(_literal_key(v) for v in expr.values))}"
    if isinstance(expr, NotIn):
        return f"not_in|{expr.field}|{','.join(sorted(_literal_key(v) for v in expr.values))}"
    if isinstance(expr, Missing):
        return f"missing|{expr.field}"
    if isinstance(expr, And):
        return "and(" + ",".join(sorted(canonical_key(i) for i in expr.items)) + ")"
    if isinstance(expr, Or):
        return "or(" + ",".join(sorted(canonical_key(i) for i in expr.items)) + ")"
    if isinstance(expr, NotNode):
        return f"not({canonical_key(expr.item)})"
    if isinstance(expr, NotTrue):
        return f"not_true({canonical_key(expr.item)})"
    return f"const|{str(expr.value).lower()}"


def _literal_key(value: str | bool) -> str:
    return json.dumps(value, ensure_ascii=False)
