from __future__ import annotations

from fractions import Fraction

from verityflow.ir.domains import continuous_domain, integer_domain, scaled_decimal_domain
from verityflow.ir.model import Cmp
from verityflow.models.dictionary import DataDictionary
from verityflow.verify.domain import (
    build_ticket_space,
    cell_from_inputs,
    condition_analysis,
    relational_witness,
    relations_equivalent,
    render_inputs,
)
from verityflow.verify.models import ComparisonMode

TICKET_DICTIONARY = DataDictionary.model_validate(
    {
        "fields": [
            {
                "name": "category",
                "type": "string",
                "nullable": True,
                "domain": ["billing", "technical", "general"],
            },
            {
                "name": "priority",
                "type": "string",
                "nullable": True,
                "domain": ["urgent", "normal"],
            },
            {"name": "wait_time", "type": "integer", "unit": "minutes", "nullable": True},
            {"name": "vip", "type": "boolean", "nullable": True},
        ],
        "destinations": [{"id": "normal_queue"}],
    }
)


def test_integer_grid_makes_strict_29_equal_to_inclusive_30() -> None:
    domain = integer_domain()

    assert relational_witness(domain, ">", Fraction(29), ">=", Fraction(30)) is None
    assert relations_equivalent(domain, ">", Fraction(29), ">=", Fraction(30))


def test_integer_grid_strictness_at_the_same_boundary_differs() -> None:
    domain = integer_domain()

    assert relational_witness(domain, ">", Fraction(29), ">=", Fraction(29)) == Fraction(29)


def test_scaled_decimal_grid_brackets_by_one_scale_step() -> None:
    one_place = scaled_decimal_domain(1)
    two_places = scaled_decimal_domain(2)

    witness = relational_witness(one_place, ">", Fraction(29), ">=", Fraction(30))
    assert witness == Fraction(291, 10)
    witness_two = relational_witness(two_places, ">", Fraction(29), ">=", Fraction(30))
    assert witness_two == Fraction(2901, 100)


def test_continuous_domain_never_reuses_integer_equivalence() -> None:
    domain = continuous_domain()

    witness = relational_witness(domain, ">", Fraction(29), ">=", Fraction(30))
    assert witness is not None
    assert Fraction(29) < witness < Fraction(30)
    assert not relations_equivalent(domain, ">", Fraction(29), ">=", Fraction(30))
    assert relational_witness(domain, ">", Fraction(29), ">=", Fraction(29)) == Fraction(29)


def test_ticket_space_uses_declared_field_grid() -> None:
    expressions = (
        Cmp(field="wait_time", op=">=", value=Fraction(30), unit=None),
        Cmp(field="wait_time", op="<", value=Fraction(5), unit=None),
    )

    space = build_ticket_space(TICKET_DICTIONARY, expressions, max_cells=10_000)

    assert space.mode is ComparisonMode.EXHAUSTIVE
    assert space.combinations_skipped == 0
    wait_values = {cell.value("wait_time") for cell in space.cells if cell.is_present("wait_time")}
    assert {Fraction(4), Fraction(5), Fraction(29), Fraction(30), Fraction(31)} <= wait_values
    assert any(not cell.is_present("wait_time") for cell in space.cells)


def test_absent_round_trips_through_rendered_inputs() -> None:
    expressions = (Cmp(field="wait_time", op=">=", value=Fraction(30), unit=None),)
    space = build_ticket_space(TICKET_DICTIONARY, expressions, max_cells=10_000)
    absent = next(cell for cell in space.cells if not cell.is_present("wait_time"))

    inputs = render_inputs(absent)
    rebuilt = cell_from_inputs(TICKET_DICTIONARY, inputs)

    assert inputs["wait_time"] is None
    assert rebuilt.key == absent.key


def test_condition_analysis_collects_cuts_and_literals() -> None:
    from verityflow.ir.model import Eq

    analysis = condition_analysis(
        (
            Cmp(field="wait_time", op=">=", value=Fraction(30), unit=None),
            Eq(field="priority", value="urgent"),
        )
    )

    assert analysis.cuts["wait_time"] == (Fraction(30),)
    assert analysis.literals["priority"] == ("urgent",)
    assert analysis.fields == ("priority", "wait_time")


def test_sampling_is_disclosed_instead_of_claimed_exhaustive() -> None:
    expressions = (Cmp(field="wait_time", op=">=", value=Fraction(30), unit=None),)

    space = build_ticket_space(TICKET_DICTIONARY, expressions, max_cells=9)

    assert space.mode is ComparisonMode.SAMPLED
    assert len(space.cells) <= 9
    assert space.combinations_skipped > 0
    assert space.combinations_total == space.checked + space.combinations_skipped
    assert "not evaluated" in space.detail
