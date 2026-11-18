"""Shared semantic-equivalence helpers for Stage 2 comparisons.

These helpers decide correspondence from behaviour over the ticket space; rule
IDs, priorities and numeric text are never treated as evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
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
    canonical_key,
)
from verityflow.ir.policy import Policy, Rule
from verityflow.mapping.semantics import equivalent_conditions
from verityflow.models.dictionary import DataDictionary
from verityflow.models.report import FindingCategory

_DIRECTIONS: dict[str, str] = {
    "<": "ascending",
    "<=": "ascending",
    ">": "descending",
    ">=": "descending",
    "==": "equality",
    "!=": "equality",
}


@dataclass(frozen=True, slots=True)
class NumericClue:
    category: FindingCategory
    detail: str


def same_behaviour(first: Expr, second: Expr, dictionary: DataDictionary, max_cells: int) -> bool:
    if canonical_key(first) == canonical_key(second):
        return True
    outcome = equivalent_conditions(first, second, dictionary, max_cells=max_cells)
    return outcome.equal is True


def firing_equivalent(
    first: Expr, second: Expr, dictionary: DataDictionary, max_cells: int
) -> bool:
    """Compare whether two conditions *fire* under ordered first-match semantics.

    A condition fires only when it evaluates to ``TRUE``; ``FALSE`` and
    ``UNKNOWN`` both fall through. Wrapping in ``NOT(NOT_TRUE(...))`` turns that
    into an exact truth comparison, so a code presence guard such as
    ``"wait_time" in ticket and ticket["wait_time"] >= 30`` matches the plain
    guarded requirement even though the guard adds a ``FALSE``/``UNKNOWN``
    distinction that cannot change which branch fires.
    """
    if canonical_key(first) == canonical_key(second):
        return True
    outcome = equivalent_conditions(
        NotNode(item=NotTrue(item=first)),
        NotNode(item=NotTrue(item=second)),
        dictionary,
        max_cells=max_cells,
    )
    return outcome.equal is True


def perfect_matching(
    left: list[Expr],
    left_destinations: list[str],
    right: list[Expr],
    right_destinations: list[str],
    dictionary: DataDictionary,
    max_cells: int,
    *,
    firing: bool = False,
) -> list[int] | None:
    """Return a same-destination, behaviour-equivalent assignment or ``None``."""
    if len(left) != len(right):
        return None
    equivalent = firing_equivalent if firing else same_behaviour
    edges: list[list[int]] = []
    for index, condition in enumerate(left):
        candidates = [
            candidate
            for candidate, (other, destination) in enumerate(
                zip(right, right_destinations, strict=True)
            )
            if destination == left_destinations[index]
            and equivalent(condition, other, dictionary, max_cells)
        ]
        if not candidates:
            return None
        edges.append(candidates)

    assignment: list[int] = [-1] * len(left)
    used: set[int] = set()

    def assign(index: int) -> bool:
        if index == len(left):
            return True
        for candidate in edges[index]:
            if candidate in used:
                continue
            used.add(candidate)
            assignment[index] = candidate
            if assign(index + 1):
                return True
            used.remove(candidate)
            assignment[index] = -1
        return False

    return assignment if assign(0) else None


def is_reordered(
    left: list[Expr],
    left_destinations: list[str],
    right: list[Expr],
    right_destinations: list[str],
    dictionary: DataDictionary,
    max_cells: int,
    *,
    firing: bool = False,
) -> bool:
    matching = perfect_matching(
        left,
        left_destinations,
        right,
        right_destinations,
        dictionary,
        max_cells,
        firing=firing,
    )
    if matching is None:
        return False
    return any(index != matched for index, matched in enumerate(matching))


def cmp_atoms(expr: Expr) -> tuple[Cmp, ...]:
    if isinstance(expr, Cmp):
        return (expr,)
    if isinstance(expr, (And, Or)):
        collected: list[Cmp] = []
        for item in expr.items:
            collected.extend(cmp_atoms(item))
        return tuple(collected)
    if isinstance(expr, (NotNode, NotTrue)):
        return cmp_atoms(expr.item)
    if isinstance(expr, (Eq, Ne, In, NotIn, Missing, Const)):
        return ()
    return ()


def single_cmp(expr: Expr) -> Cmp | None:
    atoms = cmp_atoms(expr)
    return atoms[0] if len(atoms) == 1 else None


def numeric_clue(first: Expr | Cmp, second: Expr | Cmp) -> NumericClue | None:
    """Classify a numeric boundary change between two conditions or atoms.

    A strictness change at the same value (``> 30`` versus ``>= 30``) shifts the
    effective threshold by one grid step, so it is reported as a threshold
    change, not as an operator change. Changing the comparison direction is an
    operator change.
    """
    first_atom = first if isinstance(first, Cmp) else single_cmp(first)
    second_atom = second if isinstance(second, Cmp) else single_cmp(second)
    if first_atom is None or second_atom is None:
        return None
    if first_atom.field != second_atom.field:
        return None
    if first_atom.op == second_atom.op and first_atom.value == second_atom.value:
        return None
    detail = (
        f"the boundary for {first_atom.field!r} differs on the declared field "
        f"domain ({first_atom.op} {number(first_atom.value)} vs "
        f"{second_atom.op} {number(second_atom.value)})"
    )
    if _DIRECTIONS[first_atom.op] != _DIRECTIONS[second_atom.op]:
        return NumericClue(FindingCategory.WRONG_OPERATOR, detail)
    return NumericClue(FindingCategory.THRESHOLD_CHANGED, detail)


def number(value: Fraction) -> str:
    return str(int(value)) if value.denominator == 1 else str(value)


def numeric_requirement(
    policy: Policy,
    condition: Expr | None,
    dictionary: DataDictionary,
    max_cells: int,
) -> tuple[Rule, NumericClue] | None:
    """Return the requirement boundary that differs from an implementation guard.

    A boundary defect can make the witness fall through to a different rule, so the
    finding must reference the rule whose *condition* differs, not the rule that
    happened to fire on the ticket.
    """
    if condition is None:
        return None
    for rule in policy.rules:
        clue = numeric_clue(rule.condition, condition)
        if clue is not None:
            return rule, clue
    return None


__all__ = [
    "NumericClue",
    "cmp_atoms",
    "firing_equivalent",
    "is_reordered",
    "number",
    "numeric_clue",
    "numeric_requirement",
    "perfect_matching",
    "same_behaviour",
    "single_cmp",
]
