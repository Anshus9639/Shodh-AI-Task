from __future__ import annotations

from fractions import Fraction

import pytest

from verityflow.config import Config, Limits
from verityflow.ir.evaluate import CellValue
from verityflow.models.common import ArtifactRef
from verityflow.models.dictionary import DataDictionary
from verityflow.models.manifest import CodeLink, FieldMapEntry
from verityflow.verify.python_ast import (
    CodeProgram,
    RouterLimitError,
    RouterSyntaxError,
    RouterUnsupportedError,
    compile_router,
)
from verityflow.verify.routing import RouteKind

ROUTER = '''\
"""Ticket router."""
from __future__ import annotations

MANUAL = "manual"
URGENT = "urgent"


def route(ticket):
    if "category" not in ticket:
        return MANUAL
    if ticket["priority"] == URGENT:
        return "priority"
    if ticket["wait_time"] >= 30:
        return "escalation"
    return "normal"
'''

DESTINATIONS = {
    "manual": "manual_review",
    "priority": "priority_queue",
    "escalation": "escalation_queue",
    "normal": "normal_queue",
}


def _link(**overrides: object) -> CodeLink:
    payload: dict[str, object] = {
        "entrypoint": "route",
        "param": "ticket",
        "destination_map": DESTINATIONS,
    }
    payload.update(overrides)
    return CodeLink.model_validate(payload)


def _compile(
    source: str,
    dictionary: DataDictionary,
    *,
    link: CodeLink | None = None,
    limits: Limits | None = None,
) -> CodeProgram:
    return compile_router(
        source,
        link=link or _link(),
        dictionary=dictionary,
        artifact=ArtifactRef(artifact_id="ROUTER", version="1.0"),
        limits=limits or Config().limits,
        path="router.py",
    )


def _values(
    *,
    category: str | None = "billing",
    priority: str | None = "normal",
    wait_time: Fraction | None = Fraction(10),
) -> dict[str, CellValue]:
    values: dict[str, CellValue] = {
        "category": CellValue.absent() if category is None else CellValue.present(category),
        "priority": CellValue.absent() if priority is None else CellValue.present(priority),
        "wait_time": (CellValue.absent() if wait_time is None else CellValue.present(wait_time)),
    }
    values["vip"] = CellValue.absent()
    values["amount"] = CellValue.absent()
    return values


def test_supported_router_routes_every_path(dictionary: DataDictionary) -> None:
    program = _compile(ROUTER, dictionary)

    assert program.route(_values(category=None)).destination == "manual_review"
    assert program.route(_values(priority="urgent")).destination == "priority_queue"
    assert program.route(_values(wait_time=Fraction(30))).destination == "escalation_queue"
    assert program.route(_values()).destination == "normal_queue"


def test_absent_field_subscript_is_a_runtime_error(dictionary: DataDictionary) -> None:
    source = """\
def route(ticket):
    if ticket["category"] == "billing":
        return "manual"
    return "normal"
"""
    outcome = _compile(source, dictionary).route(_values(category=None))
    assert outcome.kind is RouteKind.ERROR
    assert outcome.detail is not None and "KeyError" in outcome.detail


def test_missing_numeric_field_comparison_is_a_runtime_error(
    dictionary: DataDictionary,
) -> None:
    source = """\
def route(ticket):
    if ticket.get("wait_time") >= 30:
        return "escalation"
    return "normal"
"""
    outcome = _compile(source, dictionary).route(_values(wait_time=None))
    assert outcome.kind is RouteKind.ERROR
    assert outcome.detail is not None and "TypeError" in outcome.detail


def test_mixed_type_comparison_is_a_runtime_error(dictionary: DataDictionary) -> None:
    source = """\
def route(ticket):
    if ticket["wait_time"] >= "30":
        return "escalation"
    return "normal"
"""
    outcome = _compile(source, dictionary).route(_values())
    assert outcome.kind is RouteKind.ERROR
    assert outcome.detail is not None and "TypeError" in outcome.detail


def test_fell_through_and_returned_none_are_distinct(dictionary: DataDictionary) -> None:
    fell = _compile(
        """\
def route(ticket):
    if ticket["priority"] == "urgent":
        return "priority"
""",
        dictionary,
    ).route(_values())
    assert fell.kind is RouteKind.FALL_THROUGH
    assert fell.destination is None

    none = _compile(
        """\
def route(ticket):
    if ticket["priority"] == "urgent":
        return "priority"
    return None
""",
        dictionary,
    ).route(_values())
    assert none.kind is RouteKind.NO_MATCH


@pytest.mark.parametrize(
    "source",
    [
        """\
def route(ticket):
    for key in ticket:
        return "normal"
    return "normal"
""",
        """\
def route(ticket):
    return helper(ticket)
""",
        """\
def route(ticket):
    if ticket["wait_time"] + 1 >= 30:
        return "escalation"
    return "normal"
""",
        """\
def route(ticket):
    check = lambda: "normal"
    return check()
""",
        """\
def route(ticket):
    if ticket.priority == "urgent":
        return "priority"
    return "normal"
""",
        """\
def route(ticket):
    while True:
        return "normal"
""",
        """\
def route(ticket):
    return f"{ticket['priority']}"
""",
    ],
)
def test_unsupported_constructs_are_rejected(source: str, dictionary: DataDictionary) -> None:
    with pytest.raises(RouterUnsupportedError):
        _compile(source, dictionary)


def test_syntax_errors_are_distinct_from_unsupported(
    dictionary: DataDictionary,
) -> None:
    with pytest.raises(RouterSyntaxError) as excinfo:
        _compile("def route(ticket)\n    return 'normal'\n", dictionary)
    assert excinfo.value.lineno == 1
    with pytest.raises(RouterUnsupportedError):
        _compile("class Router:\n    pass\n", dictionary)


def test_bundle_code_is_never_executed(dictionary: DataDictionary, tmp_path) -> None:
    marker = tmp_path / "executed.txt"
    source = f"""\
import os
os.system("touch {marker.as_posix()}")

def route(ticket):
    return "normal"
"""
    with pytest.raises(RouterUnsupportedError):
        _compile(source, dictionary)
    assert not marker.exists()


def test_module_constants_can_be_literals_and_names(dictionary: DataDictionary) -> None:
    source = """\
THRESHOLD = 30
DESTINATION = "escalation"


def route(ticket):
    limit = THRESHOLD
    if ticket["wait_time"] >= limit:
        return DESTINATION
    return "normal"
"""
    program = _compile(source, dictionary)
    assert program.route(_values(wait_time=Fraction(30))).destination == "escalation_queue"


def test_membership_against_literal_collection(dictionary: DataDictionary) -> None:
    source = """\
def route(ticket):
    if ticket["category"] in {"billing", "technical"}:
        return "priority"
    return "normal"
"""
    program = _compile(source, dictionary)
    assert program.route(_values(category="technical")).destination == "priority_queue"
    assert program.route(_values(category="general")).destination == "normal_queue"


def test_field_map_converts_units_before_comparison(dictionary: DataDictionary) -> None:
    source = """\
def route(ticket):
    if ticket["wait_seconds"] >= 1800:
        return "escalation"
    return "normal"
"""
    link = _link(field_map={"wait_seconds": FieldMapEntry(field="wait_time", unit="seconds")})
    program = _compile(source, dictionary, link=link)
    assert program.route(_values(wait_time=Fraction(30))).destination == "escalation_queue"
    assert program.route(_values(wait_time=Fraction(29))).destination == "normal_queue"


def test_destination_map_translates_returned_labels(dictionary: DataDictionary) -> None:
    source = """\
def route(ticket):
    return "manual review"
"""
    outcome = _compile(source, dictionary).route(_values())
    assert outcome.kind is RouteKind.DESTINATION
    assert outcome.destination == "manual_review"


def test_non_string_and_undeclared_destinations_are_unsupported(
    dictionary: DataDictionary,
) -> None:
    program = _compile(
        """\
def route(ticket):
    return 7
""",
        dictionary,
    )
    assert program.route(_values()).kind is RouteKind.UNSUPPORTED

    undeclared = _compile(
        """\
def route(ticket):
    return "nowhere"
""",
        dictionary,
    )
    outcome = undeclared.route(_values())
    assert outcome.kind is RouteKind.UNSUPPORTED
    assert outcome.detail is not None and "undeclared destination" in outcome.detail


def test_entrypoint_and_parameter_must_match_the_link(dictionary: DataDictionary) -> None:
    with pytest.raises(RouterUnsupportedError):
        _compile(
            """\
def decide(ticket):
    return "normal"
""",
            dictionary,
        )
    with pytest.raises(RouterUnsupportedError):
        _compile(
            """\
def route(record):
    return "normal"
""",
            dictionary,
        )


def test_parameter_cannot_be_reassigned(dictionary: DataDictionary) -> None:
    with pytest.raises(RouterUnsupportedError):
        _compile(
            """\
def route(ticket):
    ticket = {}
    return "normal"
""",
            dictionary,
        )


def test_multiple_functions_are_rejected(dictionary: DataDictionary) -> None:
    with pytest.raises(RouterUnsupportedError):
        _compile(
            """\
def helper(ticket):
    return "normal"


def route(ticket):
    return "normal"
""",
            dictionary,
        )


def test_step_budget_stops_evaluation(dictionary: DataDictionary) -> None:
    limits = Limits(interpreter_max_steps=2)
    program = _compile(ROUTER, dictionary, limits=limits)
    outcome = program.route(_values())
    assert outcome.kind is RouteKind.STEP_LIMIT


def test_depth_and_node_budgets_are_enforced(dictionary: DataDictionary) -> None:
    with pytest.raises(RouterLimitError):
        _compile(ROUTER, dictionary, limits=Limits(interpreter_max_depth=3))
    with pytest.raises(RouterLimitError):
        _compile(ROUTER, dictionary, limits=Limits(interpreter_max_ast_nodes=5))
