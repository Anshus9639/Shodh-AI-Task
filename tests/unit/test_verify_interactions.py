from __future__ import annotations

from fractions import Fraction

from verityflow.ir.model import Cmp, Const, Eq
from verityflow.ir.policy import Policy, Rule
from verityflow.models.common import ArtifactRef, TextSpan
from verityflow.models.dictionary import DataDictionary
from verityflow.models.report import FindingCategory, Severity
from verityflow.requirements.compile import compile_document
from verityflow.requirements.parser import parse_requirements
from verityflow.verify.domain import build_ticket_space
from verityflow.verify.interactions import analyze_interactions

ARTIFACT = ArtifactRef(artifact_id="REQ", version="1.0")

SHADOWED_TEXT = """\
R1. Missing category -> manual review.
R2. Missing category -> normal queue.
R3. All remaining tickets -> normal queue.
"""

EXCEPTION_TEXT = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
Exception to R2: waiting >= 60 minutes -> normal queue.
R3. Otherwise, waiting >= 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
"""


def _anchor(line: int) -> TextSpan:
    return TextSpan(path="requirements.txt", line_start=line, line_end=line)


def _policy(dictionary: DataDictionary, text: str) -> Policy:
    document = parse_requirements(text, "requirements.txt", dictionary)
    policy, findings = compile_document(document, artifact=ARTIFACT)
    assert not [finding for finding in findings if finding.severity is Severity.BLOCKER]
    return policy


def _space(dictionary: DataDictionary, policy: Policy):
    return build_ticket_space(
        dictionary,
        [rule.condition for rule in policy.rules],
        max_cells=50_000,
    )


def test_shadowed_rule_is_disclosed_as_a_coverage_gap(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary, SHADOWED_TEXT)
    result = analyze_interactions(policy, _space(dictionary, policy))

    assert result.unreachable_rules == ("R2",)
    assert result.misplaced_exceptions == ()
    findings = [finding for finding in result.findings]
    assert len(findings) == 1
    assert findings[0].category is FindingCategory.INTERACTION
    assert findings[0].severity is Severity.COVERAGE_GAP
    assert findings[0].rule_ref == "R2"
    assert "never fires" in findings[0].message


def test_exception_attached_to_its_target_is_not_misplaced(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary, EXCEPTION_TEXT)
    result = analyze_interactions(policy, _space(dictionary, policy))

    assert result.unreachable_rules == ()
    assert result.misplaced_exceptions == ()
    assert result.findings == ()


def test_exception_firing_outside_its_target_is_reported(dictionary: DataDictionary) -> None:
    policy = Policy(
        candidate_id="hand-built",
        rules=(
            Rule(
                rule_ref="A",
                anchor=_anchor(1),
                condition=Eq(field="priority", value="urgent"),
                destination="priority_queue",
                precedence=0,
            ),
            Rule(
                rule_ref="A.ex",
                anchor=_anchor(2),
                condition=Cmp(field="wait_time", op=">=", value=Fraction(60), unit="minutes"),
                destination="normal_queue",
                precedence=1,
                exception_of="A",
            ),
            Rule(
                rule_ref="Z",
                anchor=_anchor(3),
                condition=Const(value=True),
                destination="normal_queue",
                precedence=2,
            ),
        ),
    )
    result = analyze_interactions(policy, _space(dictionary, policy))

    assert result.misplaced_exceptions == ("A.ex",)
    assert result.unreachable_rules == ()
    findings = [finding for finding in result.findings]
    assert len(findings) == 1
    assert findings[0].category is FindingCategory.INTERACTION
    assert findings[0].rule_ref == "A.ex"
    assert "fires where that rule does not apply" in findings[0].message
    assert "Witness:" in (findings[0].explanation or "")


def test_unreachable_exception_is_reported_as_an_interaction(dictionary: DataDictionary) -> None:
    policy = Policy(
        candidate_id="hand-built",
        rules=(
            Rule(
                rule_ref="A",
                anchor=_anchor(1),
                condition=Eq(field="priority", value="urgent"),
                destination="priority_queue",
                precedence=0,
            ),
            Rule(
                rule_ref="A.ex",
                anchor=_anchor(2),
                condition=Eq(field="priority", value="urgent"),
                destination="normal_queue",
                precedence=1,
                exception_of="A",
            ),
            Rule(
                rule_ref="Z",
                anchor=_anchor(3),
                condition=Const(value=True),
                destination="normal_queue",
                precedence=2,
            ),
        ),
    )
    result = analyze_interactions(policy, _space(dictionary, policy))

    assert result.unreachable_rules == ("A.ex",)
    assert result.findings[0].message.startswith("requirement 'A.ex' never fires")
    assert "the exception for 'A'" in result.findings[0].message
