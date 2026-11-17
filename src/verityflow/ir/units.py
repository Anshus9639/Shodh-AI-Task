from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction


class UnitError(ValueError):
    pass


class UnitFamily(StrEnum):
    DURATION = "duration"
    CURRENCY = "currency"


@dataclass(frozen=True)
class UnitDefinition:
    family: UnitFamily
    factor: Fraction


def _defs() -> dict[str, UnitDefinition]:
    duration = UnitFamily.DURATION
    currency = UnitFamily.CURRENCY
    seconds = Fraction(1)
    minute = Fraction(60)
    hour = Fraction(3600)
    day = Fraction(86400)
    cents = Fraction(1)
    usd = Fraction(100)
    return {
        "second": UnitDefinition(duration, seconds),
        "seconds": UnitDefinition(duration, seconds),
        "sec": UnitDefinition(duration, seconds),
        "minute": UnitDefinition(duration, minute),
        "minutes": UnitDefinition(duration, minute),
        "min": UnitDefinition(duration, minute),
        "hour": UnitDefinition(duration, hour),
        "hours": UnitDefinition(duration, hour),
        "hr": UnitDefinition(duration, hour),
        "day": UnitDefinition(duration, day),
        "days": UnitDefinition(duration, day),
        "cent": UnitDefinition(currency, cents),
        "cents": UnitDefinition(currency, cents),
        "usd": UnitDefinition(currency, usd),
        "dollar": UnitDefinition(currency, usd),
        "dollars": UnitDefinition(currency, usd),
    }


UNITS: dict[str, UnitDefinition] = _defs()

CANONICAL_UNITS: dict[UnitFamily, str] = {
    UnitFamily.DURATION: "seconds",
    UnitFamily.CURRENCY: "cents",
}


def get_unit(name: str) -> UnitDefinition | None:
    return UNITS.get(name.strip().lower())


def unit_names() -> tuple[str, ...]:
    return tuple(sorted(UNITS))


def normalize(value: Fraction, from_unit: str, to_unit: str) -> Fraction:
    source = get_unit(from_unit)
    target = get_unit(to_unit)
    if source is None:
        raise UnitError(f"unknown unit {from_unit!r}")
    if target is None:
        raise UnitError(f"unknown unit {to_unit!r}")
    if source.family is not target.family:
        raise UnitError(f"incompatible units {from_unit!r} and {to_unit!r}")
    return value * source.factor / target.factor


def compatible(first: str, second: str) -> bool:
    a = get_unit(first)
    b = get_unit(second)
    return a is not None and b is not None and a.family is b.family


def canonical_unit_for(family: UnitFamily) -> str:
    return CANONICAL_UNITS[family]
