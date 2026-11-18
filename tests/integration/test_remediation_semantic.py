"""General counterexample and diagnosis cases (Phase 5 development set).

The witnesses are evaluated through the same interpreters as production; each
test asserts a genuinely different outcome, exact source/implementation
evidence, and the diagnosis that the condition/execution facts support.
"""

from __future__ import annotations

from pathlib import Path

from verityflow.models.evidence import EvidenceRole
from verityflow.models.report import FindingCategory, Severity
from verityflow.verify.models import ReviewStatus
from verityflow.verify.review import review_bundle

ROOT = Path(__file__).resolve().parent.parent.parent
SEMANTIC = ROOT / "fixtures" / "remediation" / "semantic"


def _review(name: str):
    return review_bundle(SEMANTIC / name)


def _blockers(report):
    return [item for item in report.findings if item.severity is Severity.BLOCKER]


def _validated(report, comparison: str | None = None):
    return [
        witness
        for record in report.comparisons
        if comparison is None or record.name == comparison
        for witness in record.counterexamples
        if witness.validated
    ]


def _assert_source_and_implementation_evidence(finding) -> None:
    roles = {reference.evidence_role for reference in finding.evidence_refs}
    assert EvidenceRole.SOURCE_REQUIREMENT in roles
    assert roles & {
        EvidenceRole.SPECIFICATION,
        EvidenceRole.WORKFLOW_BRANCH,
        EvidenceRole.PYTHON_CODE,
    }
    assert finding.location is not None
    assert finding.recommendation


def test_integer_boundary_uses_the_declared_grid() -> None:
    report = _review("S1_integer_boundary")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    blockers = _blockers(report)
    assert len(blockers) == 1
    finding = blockers[0]
    assert finding.category is FindingCategory.THRESHOLD_CHANGED
    assert finding.rule_ref == "R3"
    assert finding.artifact_id == "ROUTER"
    _assert_source_and_implementation_evidence(finding)

    witness = _validated(report, "code")[0]
    assert witness.inputs["wait_time"] == 30
    assert witness.requirement_outcome == "destination 'escalation_queue'"
    assert witness.artifact_outcome == "destination 'normal_queue'"
    assert any("R3:TRUE" == step for step in witness.requirement_trace)
    assert any("> 30" in step for step in witness.artifact_trace)


def test_decimal_boundary_uses_the_scaled_grid() -> None:
    report = _review("S2_decimal_boundary")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    finding = _blockers(report)[0]
    assert finding.category is FindingCategory.THRESHOLD_CHANGED
    assert finding.rule_ref == "R3"
    assert finding.artifact_id == "ROUTER"
    _assert_source_and_implementation_evidence(finding)

    witnesses = _validated(report, "code")
    assert witnesses[0].inputs["wait_time"] == "59/2"
    assert all(item.requirement_outcome == "destination 'normal_queue'" for item in witnesses)
    assert all(item.artifact_outcome == "destination 'escalation_queue'" for item in witnesses)
    assert witnesses[0].artifact_trace
    assert witnesses[0].requirement_trace


def test_continuous_domain_boundary_is_classified_without_substring_matching() -> None:
    report = _review("S3_continuous_boundary")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    finding = _blockers(report)[0]
    assert finding.category is FindingCategory.THRESHOLD_CHANGED
    assert finding.rule_ref == "R3"
    witnesses = _validated(report, "code")
    assert witnesses[0].inputs["wait_time"] == 30
    assert witnesses[0].requirement_outcome == "destination 'normal_queue'"
    assert witnesses[0].artifact_outcome == "destination 'escalation_queue'"


def test_missing_value_precedence_preserves_absent_category_semantics() -> None:
    report = _review("S4_missing_value_precedence")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    finding = _blockers(report)[0]
    assert finding.category is FindingCategory.PRECEDENCE
    assert finding.rule_ref == "R1"
    witness = _validated(report, "code")[0]
    assert witness.inputs["category"] is None
    assert witness.inputs["priority"] == "urgent"
    assert witness.requirement_outcome == "destination 'manual_review'"
    assert witness.artifact_outcome == "destination 'priority_queue'"
    assert witness.requirement_trace == ("R1:TRUE",)


def test_overlapping_urgency_and_exception_is_diagnosed_as_precedence() -> None:
    report = _review("S5_exception_overlap")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    finding = _blockers(report)[0]
    assert finding.category is FindingCategory.PRECEDENCE
    assert finding.artifact_id == "ROUTER"
    witness = _validated(report, "code")[0]
    assert witness.inputs["priority"] == "urgent"
    assert witness.inputs["vip"] is True
    assert witness.requirement_outcome == "destination 'manual_review'"
    assert witness.artifact_outcome == "destination 'priority_queue'"


def test_wrong_destination_is_not_labeled_a_generic_script_disagreement() -> None:
    report = _review("S6_wrong_destination")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    assert {item.category for item in _blockers(report)} == {FindingCategory.WRONG_DESTINATION}
    finding = _blockers(report)[0]
    assert finding.rule_ref == "R2"
    _assert_source_and_implementation_evidence(finding)
    witness = _validated(report, "code")[0]
    assert witness.requirement_outcome == "destination 'priority_queue'"
    assert witness.artifact_outcome == "destination 'manual_review'"


def test_runtime_error_is_reported_without_a_behavioural_witness() -> None:
    report = _review("S7_runtime_error")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    finding = _blockers(report)[0]
    assert finding.category is FindingCategory.RUNTIME_ERROR
    assert finding.rule_ref == "R1"
    assert "KeyError" in finding.message
    _assert_source_and_implementation_evidence(finding)
    code = next(record for record in report.comparisons if record.name == "code")
    assert not code.equal
    assert all(item.inputs["category"] is None for item in code.counterexamples)


def test_multiple_applicable_rules_yield_one_finding_per_artifact() -> None:
    report = _review("S8_overlapping_numeric_rules")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    findings = _blockers(report)
    assert {item.artifact_id for item in findings} == {"SPEC", "WF", "ROUTER"}
    assert {item.category for item in findings} == {FindingCategory.MISSING_EXCEPTION}
    assert {item.rule_ref for item in findings} == {"R3.exception"}
    identities = {(item.artifact_id, item.category, item.rule_ref) for item in findings}
    assert len(findings) == len(identities)
    for finding in findings:
        assert finding.evidence_refs
        assert finding.recommendation
    witnesses = _validated(report)
    assert witnesses
    assert witnesses[0].requirement_outcome == "destination 'manual_review'"
    assert witnesses[0].artifact_outcome == "destination 'escalation_queue'"
