from __future__ import annotations

from fractions import Fraction

import pytest

from verityflow.ir.model import (
    And,
    Cmp,
    Const,
    Eq,
    In,
    Missing,
    NotIn,
    NotNode,
)
from verityflow.requirements.document import IssueCode, ParsedRuleKind
from verityflow.requirements.parser import (
    ConditionTextError,
    parse_condition_text,
    parse_requirements,
)


def _parse(text: str, dictionary):
    return parse_requirements(text, "requirements.txt", dictionary)


def test_starter_wording_parses_exactly(dictionary, starter_text: str) -> None:
    document = _parse(starter_text, dictionary)
    assert [rule.rule_ref for rule in document.rules] == ["R1", "R2", "R3", "R4"]
    first, second, third, fourth = document.rules
    assert first.condition == Missing(field="category")
    assert first.destination == "manual_review"
    assert second.condition == Eq(field="priority", value="urgent")
    assert second.destination == "priority_queue"
    assert second.had_otherwise is True
    assert third.condition == Cmp(field="wait_time", op=">=", value=Fraction(30), unit="minutes")
    assert third.destination == "escalation_queue"
    assert fourth.condition == Const(value=True)
    assert fourth.kind is ParsedRuleKind.CATCH_ALL
    assert fourth.destination == "normal_queue"
    assert document.issues == ()


def test_anchors_keep_the_raw_line(dictionary) -> None:
    text = "R1. Missing category -> manual review.\n"
    rule = _parse(text, dictionary).rules[0]
    assert rule.anchor.line_start == 1
    assert rule.anchor.line_end == 1
    assert rule.anchor.excerpt == "R1. Missing category -> manual review."
    assert rule.anchor.col_start == 1
    assert rule.anchor.col_end == len("R1. Missing category -> manual review.")
    assert rule.source_text == "R1. Missing category -> manual review."


def test_terminal_period_and_semicolon_are_accepted(dictionary) -> None:
    document = _parse(
        "R1. Missing category -> manual review.\nR2. Missing category -> manual review;\n",
        dictionary,
    )
    assert len(document.rules) == 2
    assert document.issues == ()


def test_bare_boolean_conditions(dictionary) -> None:
    document = _parse(
        "R1. vip -> priority queue.\nR2. vip and waiting >= 30 minutes -> escalation queue.\n",
        dictionary,
    )
    assert document.rules[0].condition == Eq(field="vip", value=True)
    assert document.rules[1].condition == And(
        items=(
            Eq(field="vip", value=True),
            Cmp(field="wait_time", op=">=", value=Fraction(30), unit="minutes"),
        )
    )


def test_unless_builds_a_kleene_negation(dictionary) -> None:
    rule = _parse("R1. waiting >= 30 minutes -> escalation queue, unless vip.\n", dictionary).rules[
        0
    ]
    assert rule.condition == And(
        items=(
            Cmp(field="wait_time", op=">=", value=Fraction(30), unit="minutes"),
            NotNode(item=Eq(field="vip", value=True)),
        )
    )


def test_if_route_to_form(dictionary) -> None:
    rule = _parse(
        'R1. If category is "billing" and waiting > 10 minutes, route to billing queue.\n',
        dictionary,
    ).rules[0]
    assert rule.condition == And(
        items=(
            Eq(field="category", value="billing"),
            Cmp(field="wait_time", op=">", value=Fraction(10), unit="minutes"),
        )
    )
    assert rule.destination == "billing_queue"


def test_membership_between_and_natural_comparators(dictionary) -> None:
    text = (
        'R1. category is one of ("billing", "technical") -> billing queue.\n'
        'R2. category is not one of ("general") -> normal queue.\n'
        "R3. waiting is between 10 minutes and 30 minutes inclusive -> escalation queue.\n"
        "R4. waiting is at least 30 minutes -> escalation queue.\n"
        "R5. waiting exceeds 30 minutes -> escalation queue.\n"
        "R6. category is present -> normal queue.\n"
    )
    document = _parse(text, dictionary)
    conditions = [rule.condition for rule in document.rules]
    assert conditions[0] == In(field="category", values=frozenset({"billing", "technical"}))
    assert conditions[1] == NotIn(field="category", values=frozenset({"general"}))
    assert conditions[2] == And(
        items=(
            Cmp(field="wait_time", op=">=", value=Fraction(10), unit="minutes"),
            Cmp(field="wait_time", op="<=", value=Fraction(30), unit="minutes"),
        )
    )
    assert conditions[3] == Cmp(field="wait_time", op=">=", value=Fraction(30), unit="minutes")
    assert conditions[4] == Cmp(field="wait_time", op=">", value=Fraction(30), unit="minutes")
    assert conditions[5] == NotNode(item=Missing(field="category"))


@pytest.mark.parametrize(
    ("text", "issue_code"),
    [
        (
            'R1. vip and category is "billing" or waiting >= 30 minutes -> normal queue.',
            IssueCode.AMBIGUITY,
        ),
        (
            "R1. waiting is between 10 minutes and 30 minutes -> normal queue.",
            IssueCode.AMBIGUITY,
        ),
        ("R1. mystery_field > 3 -> normal queue.", IssueCode.AMBIGUITY),
        ("R1. Missing category -> nowhere.", IssueCode.AMBIGUITY),
        ("R1. waiting >= 30 -> normal queue.", IssueCode.AMBIGUITY),
        ("R1. vip and -> normal queue.", IssueCode.UNSUPPORTED_LANGUAGE),
    ],
)
def test_unsupported_or_ambiguous_lines_are_reported(
    dictionary, text: str, issue_code: IssueCode
) -> None:
    document = _parse(text, dictionary)
    assert document.rules == ()
    assert [issue.code for issue in document.issues] == [issue_code]
    assert document.issues[0].severity.value == "COVERAGE_GAP"


def test_exceptions_get_stable_refs(dictionary) -> None:
    text = (
        "R1. waiting >= 30 minutes -> escalation queue.\n"
        "Exception to R1: if vip, route to priority queue.\n"
        'Exception to R1: if category is "billing", route to billing queue.\n'
    )
    document = _parse(text, dictionary)
    refs = [rule.rule_ref for rule in document.rules]
    assert refs == ["R1", "R1.exception", "R1.exception#2"]
    first = document.rules[1]
    assert first.exception_of == "R1"
    assert first.condition == Eq(field="vip", value=True)
    assert first.destination == "priority_queue"


def test_exception_to_unknown_rule_is_reported(dictionary) -> None:
    document = _parse("Exception to R9: if vip, route to priority queue.\n", dictionary)
    assert document.rules == ()
    assert document.issues[0].code is IssueCode.AMBIGUITY
    assert "unknown rule" in document.issues[0].message


def test_duplicate_ids_multiple_catch_alls_and_unreachable_rules(dictionary) -> None:
    duplicated = _parse(
        "R1. Missing category -> manual review.\nR1. Missing category -> manual review.\n",
        dictionary,
    )
    assert [rule.rule_ref for rule in duplicated.rules] == ["R1"]
    assert duplicated.issues[0].code is IssueCode.AMBIGUITY

    multiple = _parse(
        "R1. All remaining tickets -> normal queue.\n"
        "R2. All remaining tickets -> priority queue.\n",
        dictionary,
    )
    assert multiple.issues[0].code is IssueCode.AMBIGUITY

    unreachable = _parse(
        "R1. All remaining tickets -> normal queue.\nR2. Missing category -> manual review.\n",
        dictionary,
    )
    assert any("unreachable" in issue.message for issue in unreachable.issues)


def test_normative_prose_is_not_silently_dropped(dictionary) -> None:
    document = _parse("# Routing\n\nThe ticket must be routed within one hour.\n", dictionary)
    assert document.heading_lines == 1
    assert document.prose_lines == (3,)
    assert [issue.code for issue in document.issues] == [IssueCode.UNSUPPORTED_LANGUAGE]


def test_approval_claims_are_only_informational(dictionary) -> None:
    document = _parse(
        "R1. Missing category -> manual review.\nApproved by the risk team.\n", dictionary
    )
    assert len(document.rules) == 1
    assert [issue.code for issue in document.issues] == [IssueCode.UNTRUSTED_APPROVAL_CLAIM]
    assert document.issues[0].severity.value == "INFO"


def test_parse_condition_text(dictionary) -> None:
    expr = parse_condition_text("waiting >= 30 minutes and vip", dictionary)
    assert expr == And(
        items=(
            Cmp(field="wait_time", op=">=", value=Fraction(30), unit="minutes"),
            Eq(field="vip", value=True),
        )
    )
    with pytest.raises(ConditionTextError):
        parse_condition_text("waiting >= 30 minutes and", dictionary)
    with pytest.raises(ConditionTextError):
        parse_condition_text("mystery >= 3", dictionary)
