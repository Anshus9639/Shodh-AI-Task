from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from itertools import product

from verityflow.ir.domains import build_partition
from verityflow.ir.evaluate import CellValue, ValueState, evaluate
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
from verityflow.mapping.models import SignatureOutcome
from verityflow.models.dictionary import DataDictionary, FieldType


@dataclass(frozen=True, slots=True)
class _StateSet:
    fields: tuple[str, ...]
    states: dict[str, tuple[CellValue, ...]]
    count: int


def equivalent_conditions(
    first: Expr,
    second: Expr,
    dictionary: DataDictionary,
    *,
    max_cells: int,
) -> SignatureOutcome:
    """Compare two conditions over the union of their own atoms.

    Every supported atom is constant on each representative cell, so equal truth
    vectors over all cells establish behavioural equivalence for the supported
    language. This is a correspondence test, not a verification verdict.
    """
    try:
        state_set = _signature_states((first, second), dictionary, max_cells)
    except _StateError as exc:
        return SignatureOutcome(equal=False, reason=str(exc))
    if isinstance(state_set, str):
        return SignatureOutcome(equal=None, reason=state_set)
    for combination in product(*(state_set.states[field] for field in state_set.fields)):
        values = dict(zip(state_set.fields, combination, strict=True))
        left = evaluate(first, values)
        right = evaluate(second, values)
        if left != right:
            witness = ", ".join(
                f"{field}={_cell_text(cell)}"
                for field, cell in zip(state_set.fields, combination, strict=True)
            )
            return SignatureOutcome(
                equal=False,
                states=state_set.count,
                reason=f"truth differs at {witness or 'the empty input'}: "
                f"{left.value} != {right.value}",
            )
    return SignatureOutcome(equal=True, states=state_set.count)


class _StateError(ValueError):
    pass


def _cell_text(cell: CellValue) -> str:
    if cell.state is ValueState.PRESENT:
        return repr(cell.value)
    return cell.state.value


def _signature_states(
    exprs: tuple[Expr, ...],
    dictionary: DataDictionary,
    max_cells: int,
) -> _StateSet | str:
    cuts: dict[str, set[Fraction]] = {}
    literals: dict[str, set[str | bool]] = {}
    fields: set[str] = set()
    for expr in exprs:
        _walk(expr, cuts, literals, fields)

    states: dict[str, tuple[CellValue, ...]] = {}
    count = 1
    for name in sorted(fields):
        field = dictionary.field(name)
        if field is None:
            raise _StateError(f"field {name!r} is not declared in the data dictionary")
        if field.type is FieldType.BOOLEAN:
            values: tuple[CellValue, ...] = (CellValue.present(True), CellValue.present(False))
        elif field.type is FieldType.STRING:
            known = {value for value in literals.get(name, set()) if isinstance(value, str)}
            known.update(field.domain or ())
            other = _other_literal(known)
            values = tuple(CellValue.present(item) for item in sorted(known | {other}))
        else:
            domain = dictionary.numeric_domain(name)
            partition = build_partition(
                domain,
                tuple(sorted(cuts.get(name, set()))),
                (),
            )
            representatives = partition.representatives or (Fraction(0),)
            values = tuple(CellValue.present(item) for item in representatives)
        states[name] = (*values, CellValue.absent())
        count *= len(states[name])
        if count > max_cells:
            return (
                f"correspondence requires {count} representative states, exceeding "
                f"mapping_max_signature_cells={max_cells}; correspondence is unreviewed"
            )
    return _StateSet(fields=tuple(sorted(fields)), states=states, count=count)


def _other_literal(known: set[str]) -> str:
    candidate = "__other__"
    while candidate in known:
        candidate += "_"
    return candidate


def _walk(
    expr: Expr,
    cuts: dict[str, set[Fraction]],
    literals: dict[str, set[str | bool]],
    fields: set[str],
) -> None:
    if isinstance(expr, Cmp):
        fields.add(expr.field)
        cuts.setdefault(expr.field, set()).add(expr.value)
        return
    if isinstance(expr, (Eq, Ne)):
        fields.add(expr.field)
        literals.setdefault(expr.field, set()).add(expr.value)
        return
    if isinstance(expr, (In, NotIn)):
        fields.add(expr.field)
        literals.setdefault(expr.field, set()).update(expr.values)
        return
    if isinstance(expr, Missing):
        fields.add(expr.field)
        return
    if isinstance(expr, (And, Or)):
        for item in expr.items:
            _walk(item, cuts, literals, fields)
        return
    if isinstance(expr, (NotNode, NotTrue)):
        _walk(expr.item, cuts, literals, fields)


__all__ = ["equivalent_conditions"]
