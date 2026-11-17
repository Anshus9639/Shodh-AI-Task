from __future__ import annotations

from fractions import Fraction

import pytest

from verityflow.ir.units import (
    UnitError,
    UnitFamily,
    canonical_unit_for,
    compatible,
    get_unit,
    normalize,
    unit_names,
)


def test_duration_conversions_are_exact() -> None:
    assert normalize(Fraction(30), "minutes", "seconds") == Fraction(1800)
    assert normalize(Fraction(90), "seconds", "minutes") == Fraction(3, 2)
    assert normalize(Fraction(2), "hours", "minutes") == Fraction(120)
    assert normalize(Fraction(1), "day", "hours") == Fraction(24)


def test_currency_conversions_are_exact() -> None:
    assert normalize(Fraction(2), "usd", "cents") == Fraction(200)
    assert normalize(Fraction(250), "cents", "dollars") == Fraction(5, 2)


def test_incompatible_or_unknown_units_are_rejected() -> None:
    with pytest.raises(UnitError):
        normalize(Fraction(1), "minutes", "cents")
    with pytest.raises(UnitError):
        normalize(Fraction(1), "fortnights", "minutes")


def test_unit_metadata() -> None:
    assert get_unit("Minutes") is not None
    assert get_unit("fortnights") is None
    assert compatible("seconds", "days")
    assert not compatible("seconds", "usd")
    assert canonical_unit_for(UnitFamily.DURATION) == "seconds"
    assert "minutes" in unit_names()
