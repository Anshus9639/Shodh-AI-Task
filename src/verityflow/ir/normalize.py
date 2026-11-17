from __future__ import annotations

from pydantic import Field

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
from verityflow.ir.units import UnitError, normalize
from verityflow.models.common import FrozenModel
from verityflow.models.dictionary import DataDictionary, FieldType


class ConditionProblem(FrozenModel):
    message: str
    pointer: str = ""
    field: str | None = None


class NormalizedCondition(FrozenModel):
    expr: Expr | None = None
    problems: tuple[ConditionProblem, ...] = Field(default_factory=tuple)


def normalize_condition(
    expr: Expr,
    dictionary: DataDictionary,
    pointer: str = "",
) -> NormalizedCondition:
    problems: list[ConditionProblem] = []

    def problem(message: str, where: str, field: str | None = None) -> None:
        problems.append(ConditionProblem(message=message, pointer=where, field=field))

    def visit(node: Expr, where: str) -> Expr | None:
        if isinstance(node, Cmp):
            field = dictionary.field(node.field)
            if field is None:
                problem(f"unknown field {node.field!r}", where, node.field)
                return None
            if not field.is_numeric:
                problem(f"field {field.name!r} is not numeric", where, field.name)
                return None
            if node.unit is None:
                if field.unit is not None:
                    problem(
                        f"quantity for {field.name!r} requires a unit (declared {field.unit!r})",
                        where,
                        field.name,
                    )
                    return None
                return Cmp(field=field.name, op=node.op, value=node.value, unit=None)
            if field.unit is None:
                problem(
                    f"field {field.name!r} declares no unit but the condition uses {node.unit!r}",
                    where,
                    field.name,
                )
                return None
            try:
                value = normalize(node.value, node.unit, field.unit)
            except UnitError as exc:
                problem(str(exc), where, field.name)
                return None
            return Cmp(field=field.name, op=node.op, value=value, unit=field.unit)
        if isinstance(node, (Eq, Ne, In, NotIn)):
            field = dictionary.field(node.field)
            if field is None:
                problem(f"unknown field {node.field!r}", where, node.field)
                return None
            if field.is_numeric:
                problem(
                    f"numeric field {field.name!r} requires a comparison, not equality/membership",
                    where,
                    field.name,
                )
                return None
            if isinstance(node, (In, NotIn)):
                for literal in node.values:
                    if not _literal_matches(field.type, literal):
                        problem(
                            f"literal {literal!r} does not match field type {field.type.value!r}",
                            where,
                            field.name,
                        )
                        return None
                return node.model_copy(update={"field": field.name})
            if not _literal_matches(field.type, node.value):
                problem(
                    f"literal {node.value!r} does not match field type {field.type.value!r}",
                    where,
                    field.name,
                )
                return None
            return node.model_copy(update={"field": field.name})
        if isinstance(node, Missing):
            field = dictionary.field(node.field)
            if field is None:
                problem(f"unknown field {node.field!r}", where, node.field)
                return None
            return node.model_copy(update={"field": field.name})
        if isinstance(node, (And, Or)):
            items: list[Expr] = []
            for index, item in enumerate(node.items):
                normalized = visit(item, f"{where}/{node.kind}/{index}")
                if normalized is None:
                    return None
                items.append(normalized)
            return node.model_copy(update={"items": tuple(items)})
        if isinstance(node, NotNode):
            inner = visit(node.item, f"{where}/not")
            if inner is None:
                return None
            return NotNode(item=inner)
        if isinstance(node, NotTrue):
            inner = visit(node.item, f"{where}/not_true")
            if inner is None:
                return None
            return NotTrue(item=inner)
        if isinstance(node, Const):
            return node
        problem(f"unsupported expression node {type(node).__name__}", where)
        return None

    normalized_expr = visit(expr, pointer)
    return NormalizedCondition(expr=normalized_expr, problems=tuple(problems))


def _literal_matches(field_type: FieldType, value: str | bool) -> bool:
    if field_type is FieldType.STRING:
        return isinstance(value, str)
    if field_type is FieldType.BOOLEAN:
        return isinstance(value, bool)
    return False
