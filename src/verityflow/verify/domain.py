"""Ticket-space enumeration for Stage 2 behavioural verification.

The support-ticket dictionary declares ``wait_time`` as an ``integer`` field with
unit ``minutes``. That declared grid matters for semantic equivalence:

* integer grid (step 1): ``waiting > 29`` and ``waiting >= 30`` are equivalent,
  because no representable ticket value lies strictly between 29 and 30.
* scaled-decimal grid (step 10**-scale): ``> 29`` is equivalent to
  ``>= 29 + 10**-scale``, not necessarily ``>= 30``.
* continuous domain (no step): ``> 29`` is *not* equivalent to ``>= 30``
  (witness 29.5) and not equivalent to ``>= 29`` (witness 29).

Only the integer grid justifies rewriting ``> 29`` as ``>= 30``. Domain
partitioning below therefore derives every boundary from the declared
``FieldDecl`` and the numeric literals that actually occur in the compared
conditions; it never assumes integer semantics for decimal or continuous
fields.

Absent values are represented once: ``NULL`` and a missing key are
behaviourally identical throughout the supported IR, workflow and router
subset, so enumerating ``ABSENT`` covers both states.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from itertools import product

from verityflow.ir.domains import (
    NumericDomain,
    build_partition,
    continuous_domain,
    integer_domain,
    scaled_decimal_domain,
)
from verityflow.ir.evaluate import CellValue
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
from verityflow.models.dictionary import DataDictionary, FieldDecl, FieldType
from verityflow.verify.models import ComparisonMode

_NUMERIC_OPS: dict[str, Callable[[Fraction, Fraction], bool]] = {
    "<": lambda candidate, value: candidate < value,
    "<=": lambda candidate, value: candidate <= value,
    ">": lambda candidate, value: candidate > value,
    ">=": lambda candidate, value: candidate >= value,
    "==": lambda candidate, value: candidate == value,
    "!=": lambda candidate, value: candidate != value,
}


@dataclass(frozen=True, slots=True)
class ConditionAnalysis:
    cuts: dict[str, tuple[Fraction, ...]]
    literals: dict[str, tuple[str | bool, ...]]
    fields: tuple[str, ...]


def condition_analysis(expressions: Iterable[Expr]) -> ConditionAnalysis:
    cuts: dict[str, set[Fraction]] = {}
    literals: dict[str, set[str | bool]] = {}
    fields: set[str] = set()
    for expression in expressions:
        _walk(expression, cuts, literals, fields)
    return ConditionAnalysis(
        cuts={name: tuple(sorted(values)) for name, values in cuts.items()},
        literals={name: tuple(sorted(values, key=repr)) for name, values in literals.items()},
        fields=tuple(sorted(fields)),
    )


def relation_holds(op: str, value: Fraction, candidate: Fraction) -> bool:
    return _NUMERIC_OPS[op](candidate, value)


def relational_witness(
    domain: NumericDomain,
    first_op: str,
    first_value: Fraction,
    second_op: str,
    second_value: Fraction,
) -> Fraction | None:
    """Return a representable value where two numeric relations differ, else ``None``.

    The candidate set is the behavioural partition induced by both thresholds on
    the *declared* domain: integer grids bracket the thresholds by one, decimal
    grids by one scale step, continuous domains include rational midpoints.
    """
    partition = build_partition(domain, tuple(sorted({first_value, second_value})), ())
    for representative in partition.representatives:
        first = relation_holds(first_op, first_value, representative)
        second = relation_holds(second_op, second_value, representative)
        if first != second:
            return representative
    return None


def relations_equivalent(
    domain: NumericDomain,
    first_op: str,
    first_value: Fraction,
    second_op: str,
    second_value: Fraction,
) -> bool:
    return relational_witness(domain, first_op, first_value, second_op, second_value) is None


@dataclass(frozen=True, slots=True)
class TicketCell:
    values: tuple[tuple[str, CellValue], ...]

    def mapping(self) -> dict[str, CellValue]:
        return dict(self.values)

    @property
    def key(self) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(
            (name, cell.state.value, _kind(cell), _text(cell)) for name, cell in self.values
        )

    def is_present(self, field: str) -> bool:
        cell = dict(self.values).get(field)
        return cell is not None and cell.state.value == "present"

    def value(self, field: str) -> object | None:
        cell = dict(self.values).get(field)
        return cell.value if cell is not None else None


@dataclass(frozen=True, slots=True)
class TicketSpace:
    cells: tuple[TicketCell, ...]
    mode: ComparisonMode
    combinations_total: int
    combinations_skipped: int
    detail: str
    fields: tuple[str, ...]

    @property
    def exhaustive(self) -> bool:
        return self.mode is ComparisonMode.EXHAUSTIVE

    @property
    def checked(self) -> int:
        return len(self.cells)


def build_ticket_space(
    dictionary: DataDictionary,
    expressions: Iterable[Expr],
    *,
    max_cells: int,
) -> TicketSpace:
    analysis = condition_analysis(expressions)
    fields: dict[str, tuple[CellValue, ...]] = {}
    for field in sorted(dictionary.fields, key=lambda item: item.name):
        cuts = analysis.cuts.get(field.name, ())
        literals = analysis.literals.get(field.name, ())
        fields[field.name] = _field_states(field, cuts, literals)

    names = tuple(fields)
    total = 1
    for name in names:
        total *= len(fields[name])
    if total <= max_cells:
        cells = tuple(
            TicketCell(tuple(zip(names, combination, strict=True)))
            for combination in product(*(fields[name] for name in names))
        )
        return TicketSpace(
            cells=cells,
            mode=ComparisonMode.EXHAUSTIVE,
            combinations_total=total,
            combinations_skipped=0,
            detail=f"all {total} representative ticket combinations were evaluated",
            fields=names,
        )

    sampled = _pairwise_sample(names, fields, total, max_cells)
    return TicketSpace(
        cells=sampled,
        mode=ComparisonMode.SAMPLED,
        combinations_total=total,
        combinations_skipped=total - len(sampled),
        detail=(
            f"{len(sampled)} of {total} representative ticket combinations were evaluated "
            "(baseline, every single-field variation and deterministic field pairs); "
            f"{total - len(sampled)} combinations were not evaluated"
        ),
        fields=names,
    )


def render_inputs(cell: TicketCell) -> dict[str, str | bool | int | None]:
    rendered: dict[str, str | bool | int | None] = {}
    for name, cell_value in cell.values:
        if cell_value.state.value != "present":
            rendered[name] = None
        else:
            value = cell_value.value
            if isinstance(value, Fraction):
                rendered[name] = int(value) if value.denominator == 1 else str(value)
            elif isinstance(value, bool):
                rendered[name] = value
            else:
                rendered[name] = str(value)
    return rendered


def cell_from_inputs(
    dictionary: DataDictionary, inputs: Mapping[str, str | bool | int | None]
) -> TicketCell:
    values: list[tuple[str, CellValue]] = []
    for field in sorted(dictionary.fields, key=lambda item: item.name):
        raw = inputs.get(field.name)
        if raw is None:
            values.append((field.name, CellValue.absent()))
            continue
        values.append((field.name, CellValue.present(_coerce(field, raw))))
    return TicketCell(tuple(values))


def _coerce(field: FieldDecl, raw: str | bool | int) -> Fraction | str | bool:
    if field.type is FieldType.BOOLEAN:
        if not isinstance(raw, bool):
            raise ValueError(f"field {field.name!r} expects a boolean, got {raw!r}")
        return raw
    if field.type is FieldType.STRING:
        if not isinstance(raw, str):
            raise ValueError(f"field {field.name!r} expects a string, got {raw!r}")
        return raw
    if isinstance(raw, bool):
        raise ValueError(f"field {field.name!r} expects a number, got {raw!r}")
    if isinstance(raw, Fraction):
        return raw
    return Fraction(str(raw))


def _field_states(
    field: FieldDecl,
    cuts: tuple[Fraction, ...],
    literals: tuple[str | bool, ...],
) -> tuple[CellValue, ...]:
    if field.type is FieldType.BOOLEAN:
        present: tuple[CellValue, ...] = (CellValue.present(True), CellValue.present(False))
    elif field.type is FieldType.STRING:
        known = {value for value in literals if isinstance(value, str)}
        known.update(field.domain or ())
        other = _other_literal(known)
        present = tuple(CellValue.present(item) for item in sorted(known | {other}))
    else:
        domain = _domain_of(field)
        partition = build_partition(domain, tuple(sorted(set(cuts))), ())
        representatives = partition.representatives or (Fraction(0),)
        present = tuple(CellValue.present(item) for item in _dedupe(representatives))
    if field.nullable:
        return (*present, CellValue.absent())
    return present


def _domain_of(field: FieldDecl) -> NumericDomain:
    if field.type is FieldType.INTEGER:
        return integer_domain(field.min, field.max)
    if field.scale is not None:
        return scaled_decimal_domain(field.scale, field.min, field.max)
    return continuous_domain(field.min, field.max)


def _dedupe(values: Iterable[Fraction]) -> tuple[Fraction, ...]:
    seen: set[Fraction] = set()
    ordered: list[Fraction] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _other_literal(known: set[str]) -> str:
    candidate = "__other__"
    while candidate in known:
        candidate += "_"
    return candidate


def _pairwise_sample(
    names: tuple[str, ...],
    fields: dict[str, tuple[CellValue, ...]],
    total: int,
    max_cells: int,
) -> tuple[TicketCell, ...]:
    baseline = {name: fields[name][0] for name in names}
    ordered: list[TicketCell] = []
    seen: set[tuple[tuple[str, str, str, str], ...]] = set()

    def add(values: Mapping[str, CellValue]) -> None:
        if len(ordered) >= max_cells:
            return
        cell = TicketCell(tuple((name, values[name]) for name in names))
        if cell.key in seen:
            return
        seen.add(cell.key)
        ordered.append(cell)

    add(baseline)
    for name in names:
        for value in fields[name]:
            add({**baseline, name: value})
    for first_index, first_name in enumerate(names):
        for second_name in names[first_index + 1 :]:
            for first_value in fields[first_name]:
                for second_value in fields[second_name]:
                    if len(ordered) >= max_cells:
                        return tuple(ordered)
                    add({**baseline, first_name: first_value, second_name: second_value})
    return tuple(ordered[:max_cells])


def _kind(cell: CellValue) -> str:
    value = cell.value
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, Fraction):
        return "number"
    if isinstance(value, str):
        return "string"
    return "none"


def _text(cell: CellValue) -> str:
    return "" if cell.value is None else repr(cell.value)


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


__all__ = [
    "ConditionAnalysis",
    "TicketCell",
    "TicketSpace",
    "build_ticket_space",
    "cell_from_inputs",
    "condition_analysis",
    "relation_holds",
    "relational_witness",
    "relations_equivalent",
    "render_inputs",
]
