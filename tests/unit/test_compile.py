from __future__ import annotations

from fractions import Fraction

from verityflow.ir.model import And, Cmp, Const, Eq
from verityflow.models.report import FindingCategory, Severity
from verityflow.requirements.compile import compile_document
from verityflow.requirements.parser import parse_requirements

STARTER = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, waiting >= 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
"""


def _compile(text: str, dictionary):
    return compile_document(parse_requirements(text, "requirements.txt", dictionary))


def test_starter_policy_is_dense_and_clean(dictionary) -> None:
    policy, findings = _compile(STARTER, dictionary)
    assert findings == ()
    assert [rule.rule_ref for rule in policy.rules] == ["R1", "R2", "R3", "R4"]
    assert [rule.precedence for rule in policy.rules] == [0, 1, 2, 3]
    assert policy.default_rule is not None
    assert policy.default_rule.rule_ref == "R4"
    assert policy.candidate_id == "base"
    assert policy.diagnostic_only is False


def test_exception_is_inserted_before_its_target(dictionary) -> None:
    text = (
        "R1. waiting >= 30 minutes -> escalation queue.\n"
        "Exception to R1: if vip, route to priority queue.\n"
        "R2. All remaining tickets -> normal queue.\n"
    )
    policy, findings = _compile(text, dictionary)
    assert findings == ()
    refs = [rule.rule_ref for rule in policy.rules]
    assert refs == ["R1.exception", "R1", "R2"]
    exception = policy.rules[0]
    assert exception.exception_of == "R1"
    assert exception.condition == And(
        items=(
            Cmp(field="wait_time", op=">=", value=Fraction(30), unit="minutes"),
            Eq(field="vip", value=True),
        )
    )
    assert exception.destination == "priority_queue"


def test_exception_on_catch_all_keeps_only_its_own_condition(dictionary) -> None:
    text = (
        "R1. All remaining tickets -> normal queue.\n"
        "Exception to R1: if vip, route to priority queue.\n"
    )
    policy, findings = _compile(text, dictionary)
    assert findings == ()
    exception = policy.rules[0]
    assert exception.rule_ref == "R1.exception"
    assert exception.condition == Eq(field="vip", value=True)


def test_duplicate_rule_ids_are_reported_once_used_to_first(dictionary) -> None:
    text = (
        "R1. Missing category -> manual review.\n"
        "R1. vip -> priority queue.\n"
        "R2. All remaining tickets -> normal queue.\n"
    )
    policy, findings = _compile(text, dictionary)
    assert [rule.rule_ref for rule in policy.rules] == ["R1", "R2"]
    assert policy.rules[0].destination == "manual_review"
    assert any(finding.category is FindingCategory.AMBIGUITY for finding in findings)


def test_missing_catch_all_is_a_warning(dictionary) -> None:
    policy, findings = _compile("R1. Missing category -> manual review.\n", dictionary)
    assert len(policy.rules) == 1
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].category is FindingCategory.STRUCTURAL
    assert policy.default_rule is None


def test_rules_after_a_catch_all_are_reported(dictionary) -> None:
    text = "R1. All remaining tickets -> normal queue.\nR2. Missing category -> manual review.\n"
    policy, findings = _compile(text, dictionary)
    assert any(
        finding.category is FindingCategory.PRECEDENCE and finding.severity is Severity.COVERAGE_GAP
        for finding in findings
    )
    # Nothing is silently discarded; the unreachable rule remains visible.
    assert [rule.rule_ref for rule in policy.rules] == ["R1", "R2"]


def test_multiple_catch_alls_keep_only_the_first(dictionary) -> None:
    text = (
        "R1. All remaining tickets -> normal queue.\nR2. All remaining tickets -> priority queue.\n"
    )
    policy, findings = _compile(text, dictionary)
    assert [rule.rule_ref for rule in policy.rules] == ["R1"]
    assert any(finding.category is FindingCategory.AMBIGUITY for finding in findings)


def test_document_without_rules_is_a_coverage_gap(dictionary) -> None:
    policy, findings = _compile("# Requirements\n\nProse only.\n", dictionary)
    assert policy.rules == ()
    assert any(
        finding.category is FindingCategory.STRUCTURAL and finding.severity is Severity.COVERAGE_GAP
        for finding in findings
    )


def test_approval_claims_do_not_create_policy(dictionary) -> None:
    text = (
        "R1. Missing category -> manual review.\n"
        "Approved by the risk team.\n"
        "R2. All remaining tickets -> normal queue.\n"
    )
    policy, findings = _compile(text, dictionary)
    assert [rule.rule_ref for rule in policy.rules] == ["R1", "R2"]
    claim = [f for f in findings if f.category is FindingCategory.UNTRUSTED_APPROVAL_CLAIM]
    assert len(claim) == 1
    assert claim[0].severity is Severity.INFO


def test_catch_all_condition_is_exactly_true(dictionary) -> None:
    policy, _ = _compile(STARTER, dictionary)
    assert policy.rules[-1].condition == Const(value=True)
