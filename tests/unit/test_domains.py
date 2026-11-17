from __future__ import annotations

from fractions import Fraction
from itertools import pairwise

import pytest

from verityflow.ir.domains import (
    DomainKind,
    NumericDomain,
    PartitionCell,
    build_partition,
    continuous_domain,
    integer_domain,
    scaled_decimal_domain,
)
from verityflow.ir.evaluate import CellValue, evaluate
from verityflow.ir.model import Cmp


def test_domain_validation() -> None:
    with pytest.raises(ValueError):
        integer_domain(Fraction(10), Fraction(0))
    with pytest.raises(ValueError):
        NumericDomain(DomainKind.CONTINUOUS, step=Fraction(1))
    with pytest.raises(ValueError):
        NumericDomain(DomainKind.INTEGER, step=Fraction(2))
    with pytest.raises(ValueError):
        scaled_decimal_domain(-1)


def test_unbounded_integer_partition_brackets_the_cut() -> None:
    partition = build_partition(integer_domain(), (Fraction(30),))
    assert {Fraction(29), Fraction(30), Fraction(31)} <= set(partition.representatives)
    assert partition.cells[0].lower is None
    assert partition.cells[-1].upper is None
    assert partition.boundaries == (Fraction(29), Fraction(30), Fraction(31))


def test_bounded_integer_partition_respects_bounds() -> None:
    partition = build_partition(integer_domain(Fraction(0), Fraction(100)), (Fraction(30),))
    representatives = set(partition.representatives)
    assert {Fraction(29), Fraction(30), Fraction(31)} <= representatives
    assert min(representatives) >= 0
    assert max(representatives) <= 100
    assert all(cell.lower is not None and cell.upper is not None for cell in partition.cells)


def test_decimal_partition_uses_adjacent_representable_values() -> None:
    # 30.005 lies between the representable values 30.00 and 30.01.
    partition = build_partition(scaled_decimal_domain(2), (Fraction(30005, 1000),))
    representatives = set(partition.representatives)
    assert Fraction(3000, 100) in representatives
    assert Fraction(3001, 100) in representatives
    assert partition.domain.step == Fraction(1, 100)


def test_continuous_partition_has_witness_in_every_open_interval() -> None:
    partition = build_partition(continuous_domain(Fraction(0), Fraction(10)), (Fraction(5, 2),))
    assert all(cell.lower is not None and cell.upper is not None for cell in partition.cells)
    boundaries = partition.boundaries
    assert boundaries == (Fraction(0), Fraction(5, 2), Fraction(10))
    for lower, upper in pairwise(boundaries):
        witnesses = [
            cell.representative
            for cell in partition.cells
            if cell.lower == lower and cell.upper == upper and not cell.lower_inclusive
        ]
        assert witnesses, f"open interval ({lower}, {upper}) has no witness"
        assert all(lower < witness < upper for witness in witnesses)


def test_continuous_partition_records_unbounded_ends() -> None:
    partition = build_partition(continuous_domain(), (Fraction(5, 2),))
    assert any(cell.lower is None for cell in partition.cells)
    assert any(cell.upper is None for cell in partition.cells)


def test_empty_partition_still_has_a_representative() -> None:
    integer_cell = build_partition(integer_domain())
    continuous_cell = build_partition(continuous_domain())
    assert integer_cell.representatives == (Fraction(0),)
    assert continuous_cell.representatives == (Fraction(0),)
    assert integer_cell.cells[0].lower is None
    assert integer_cell.cells[0].upper is None


def _contains(cell: PartitionCell, value: Fraction) -> bool:
    if cell.lower is not None:
        if value < cell.lower or (value == cell.lower and not cell.lower_inclusive):
            return False
    if cell.upper is not None:
        if value > cell.upper or (value == cell.upper and not cell.upper_inclusive):
            return False
    return True


def _samples(cell: PartitionCell, step: Fraction | None) -> set[Fraction]:
    values = {cell.representative}
    if step is None:
        if cell.lower is not None and cell.upper is not None:
            gap = cell.upper - cell.lower
            candidates = {
                cell.lower + gap / 3,
                cell.lower + 2 * gap / 3,
            }
        elif cell.upper is not None:
            candidates = {cell.upper - 1, cell.upper - Fraction(1, 2)}
        elif cell.lower is not None:
            candidates = {cell.lower + 1, cell.lower + Fraction(1, 2)}
        else:
            candidates = {Fraction(-1), Fraction(1)}
    else:
        candidates = {
            cell.representative - step,
            cell.representative + step,
        }
    values.update(value for value in candidates if _contains(cell, value))
    return values


@pytest.mark.parametrize(
    "domain",
    [
        integer_domain(),
        integer_domain(Fraction(0), Fraction(100)),
        continuous_domain(),
        continuous_domain(Fraction(0), Fraction(10)),
        scaled_decimal_domain(2),
    ],
)
def test_every_supported_atom_is_constant_on_every_cell(domain: NumericDomain) -> None:
    cut = Fraction(30)
    partition = build_partition(domain, (cut,))
    atoms = (
        Cmp(field="wait", op="<", value=cut, unit="minutes"),
        Cmp(field="wait", op="<=", value=cut, unit="minutes"),
        Cmp(field="wait", op=">", value=cut, unit="minutes"),
        Cmp(field="wait", op=">=", value=cut, unit="minutes"),
        Cmp(field="wait", op="==", value=cut, unit="minutes"),
        Cmp(field="wait", op="!=", value=cut, unit="minutes"),
    )
    for cell in partition.cells:
        for sample in _samples(cell, domain.step):
            truth = {atom: evaluate(atom, {"wait": CellValue.present(sample)}) for atom in atoms}
            reference = {
                atom: evaluate(atom, {"wait": CellValue.present(cell.representative)})
                for atom in atoms
            }
            assert truth == reference


def test_cells_are_ordered_and_disjoint() -> None:
    partition = build_partition(integer_domain(Fraction(-5), Fraction(5)), (Fraction(0),))
    points = [cell.representative for cell in partition.cells]
    assert points == sorted(points)
    seen: set[Fraction] = set()
    for cell in partition.cells:
        assert cell.representative not in seen
        seen.add(cell.representative)
        if cell.lower is not None and cell.upper is not None and cell.lower != cell.upper:
            assert cell.lower < cell.representative < cell.upper
