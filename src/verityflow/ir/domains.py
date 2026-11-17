from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction


class DomainKind(StrEnum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    CONTINUOUS = "continuous"


@dataclass(frozen=True)
class NumericDomain:
    kind: DomainKind
    minimum: Fraction | None = None
    maximum: Fraction | None = None
    step: Fraction | None = None

    def __post_init__(self) -> None:
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("domain minimum exceeds maximum")
        if self.kind is DomainKind.CONTINUOUS:
            if self.step is not None:
                raise ValueError("continuous domains have no grid step")
        elif self.step is None or self.step <= 0:
            raise ValueError("discrete domains require a positive step")
        if self.kind is DomainKind.INTEGER and self.step != Fraction(1):
            raise ValueError("integer domains require step 1")


@dataclass(frozen=True)
class PartitionCell:
    representative: Fraction
    lower: Fraction | None
    upper: Fraction | None
    lower_inclusive: bool
    upper_inclusive: bool


@dataclass(frozen=True)
class NumericPartition:
    domain: NumericDomain
    boundaries: tuple[Fraction, ...]
    cells: tuple[PartitionCell, ...]

    @property
    def representatives(self) -> tuple[Fraction, ...]:
        return tuple(cell.representative for cell in self.cells)


def integer_domain(
    minimum: Fraction | None = None, maximum: Fraction | None = None
) -> NumericDomain:
    return NumericDomain(DomainKind.INTEGER, minimum, maximum, Fraction(1))


def scaled_decimal_domain(
    scale: int,
    minimum: Fraction | None = None,
    maximum: Fraction | None = None,
) -> NumericDomain:
    if scale < 0:
        raise ValueError("scale must be non-negative")
    return NumericDomain(DomainKind.DECIMAL, minimum, maximum, Fraction(1, 10**scale))


def continuous_domain(
    minimum: Fraction | None = None, maximum: Fraction | None = None
) -> NumericDomain:
    return NumericDomain(DomainKind.CONTINUOUS, minimum, maximum, None)


def _floor_to(value: Fraction, step: Fraction) -> Fraction:
    return (value // step) * step


def _ceil_to(value: Fraction, step: Fraction) -> Fraction:
    return -((-value) // step) * step


def build_partition(
    domain: NumericDomain,
    cuts: tuple[Fraction, ...] = (),
    literals: tuple[Fraction, ...] = (),
) -> NumericPartition:
    """Build one representative per behavioural cell.

    Discrete domains bracket every cut and literal between adjacent representable
    values; continuous domains use the cut/literal itself plus rational midpoint
    witnesses for open intervals. Every supported atom is constant on each cell.
    """
    step = domain.step
    continuous = domain.kind is DomainKind.CONTINUOUS
    boundaries: set[Fraction] = set()

    def add(value: Fraction | None) -> None:
        if value is None:
            return
        if domain.minimum is not None and value < domain.minimum:
            return
        if domain.maximum is not None and value > domain.maximum:
            return
        boundaries.add(value)

    add(domain.minimum)
    add(domain.maximum)

    def add_point(value: Fraction) -> None:
        if continuous:
            add(value)
            return
        if step is None:
            add(value)
            return
        low = _floor_to(value, step)
        high = _ceil_to(value, step)
        for boundary in (low, high):
            add(boundary)
            add(boundary - step)
            add(boundary + step)

    for cut in cuts:
        add_point(cut)
    for literal in literals:
        add_point(literal)

    ordered = sorted(boundaries)
    if not ordered:
        cell = PartitionCell(Fraction(0), None, None, False, False)
        return NumericPartition(domain, (), (cell,))

    cells: list[PartitionCell] = []
    if domain.minimum is None:
        first = ordered[0]
        offset = step if step is not None else Fraction(1)
        cells.append(PartitionCell(first - offset, None, first, False, False))

    for index, boundary in enumerate(ordered):
        cells.append(PartitionCell(boundary, boundary, boundary, True, True))
        if index + 1 >= len(ordered):
            continue
        following = ordered[index + 1]
        gap = following - boundary
        if continuous and gap > 0:
            cells.append(
                PartitionCell((boundary + following) / 2, boundary, following, False, False)
            )
        elif not continuous and step is not None and gap > step:
            cells.append(PartitionCell(boundary + step, boundary, following, False, False))

    if domain.maximum is None:
        last = ordered[-1]
        offset = step if step is not None else Fraction(1)
        cells.append(PartitionCell(last + offset, last, None, False, False))

    cells.sort(key=lambda cell: cell.representative)
    return NumericPartition(domain, tuple(ordered), tuple(cells))
