"""Approved-revision correctness on the post-remediation development bundles.

These tests exercise authority, revision binding, unit normalization and
sequential application. Every expected routing outcome is reasoned from the
bundle bytes, not from reviewer output.
"""

from __future__ import annotations

from pathlib import Path

from verityflow.models.evidence import EvidenceRole
from verityflow.models.report import FindingCategory, Severity
from verityflow.verify.models import ReviewStatus
from verityflow.verify.review import review_bundle

ROOT = Path(__file__).resolve().parent.parent.parent
APPROVALS = ROOT / "fixtures" / "remediation" / "approvals"


def _review(name: str):
    return review_bundle(APPROVALS / name)


def _blockers(report):
    return [item for item in report.findings if item.severity is Severity.BLOCKER]


def _categories(report) -> set[FindingCategory]:
    return {item.category for item in report.findings}


def _validated(report):
    return [
        witness
        for comparison in report.comparisons
        for witness in comparison.counterexamples
        if witness.validated
    ]


def test_original_policy_passes_without_changes() -> None:
    report = _review("A_original_30")

    assert report.status is ReviewStatus.PASS
    assert report.exhaustive
    assert report.applied_changes == ()
    assert report.findings == ()
    assert report.conflict_analysis == ()


def test_single_approved_threshold_applies_exactly_once() -> None:
    report = _review("B_approved_25_impl_25")

    assert report.status is ReviewStatus.PASS
    assert report.applied_changes == ("C1@1",)
    assert report.exhaustive
    assert _validated(report) == []


def test_approved_threshold_with_lagging_implementation_is_a_blocker() -> None:
    report = _review("C_approved_25_impl_30")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    assert report.applied_changes == ("C1@1",)
    findings = [
        item for item in _blockers(report) if item.category is FindingCategory.THRESHOLD_CHANGED
    ]
    assert {item.artifact_id for item in findings} == {"SPEC", "WF", "ROUTER"}
    assert all(item.rule_ref == "R3" for item in findings)
    for finding in findings:
        assert finding.location is not None
        assert finding.recommendation
        roles = {reference.evidence_role for reference in finding.evidence_refs}
        assert EvidenceRole.SOURCE_REQUIREMENT in roles
        assert roles & {
            EvidenceRole.SPECIFICATION,
            EvidenceRole.WORKFLOW_BRANCH,
            EvidenceRole.PYTHON_CODE,
        }

    witnesses = _validated(report)
    assert witnesses
    assert all(item.inputs["wait_time"] in {25, 26, 27} for item in witnesses)
    assert all(item.requirement_outcome == "destination 'escalation_queue'" for item in witnesses)
    assert all(item.artifact_outcome == "destination 'normal_queue'" for item in witnesses)


def test_unapproved_change_is_disclosed_without_being_applied() -> None:
    report = _review("D_unapproved_25_impl_30")

    assert report.status is ReviewStatus.PASS
    assert report.applied_changes == ()
    info = [item for item in report.findings if item.category is FindingCategory.UNAPPROVED_CHANGE]
    assert len(info) == 1
    assert info[0].severity is Severity.INFO
    assert _blockers(report) == []


def test_change_bound_to_a_different_revision_is_stale_not_behavioural() -> None:
    report = _review("E_wrong_revision_binding")

    assert report.status is ReviewStatus.INCOMPLETE
    assert report.applied_changes == ()
    assert FindingCategory.STALE_CHANGE in _categories(report)
    assert FindingCategory.THRESHOLD_CHANGED not in _categories(report)
    stale = next(item for item in report.findings if item.category is FindingCategory.STALE_CHANGE)
    assert stale.severity is Severity.COVERAGE_GAP


def test_operator_direction_error_is_classified_from_exact_boundaries() -> None:
    report = _review("F_wrong_operator")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    assert {item.category for item in _blockers(report)} == {FindingCategory.WRONG_OPERATOR}
    blocker = _blockers(report)[0]
    assert blocker.artifact_id == "ROUTER"
    assert blocker.rule_ref == "R3"
    witnesses = {item.inputs["wait_time"] for item in _validated(report)}
    assert {28, 30} <= witnesses
    outcomes = {(item.requirement_outcome, item.artifact_outcome) for item in _validated(report)}
    assert ("destination 'escalation_queue'", "destination 'normal_queue'") in outcomes
    assert ("destination 'normal_queue'", "destination 'escalation_queue'") in outcomes


def test_unit_normalized_change_is_applied_in_field_units() -> None:
    report = _review("G_unit_conversion")

    assert report.status is ReviewStatus.PASS
    assert report.applied_changes == ("C1@1",)
    assert _validated(report) == []


def test_sequential_changes_apply_in_order_once_each() -> None:
    report = _review("H_sequential_changes")

    assert report.status is ReviewStatus.PASS
    assert report.applied_changes == ("C1@1", "C2@1")
    assert _validated(report) == []


def test_rejecting_supersession_removes_authority_and_keeps_base_policy() -> None:
    report = _review("L_valid_supersession")

    assert report.status is ReviewStatus.PASS
    assert report.applied_changes == ()
    assert FindingCategory.APPROVAL_IGNORED in _categories(report)
    assert FindingCategory.REJECTED_CHANGE in _categories(report)
    assert _blockers(report) == []


def test_hash_mismatch_is_invalid_approval_and_grants_no_authority() -> None:
    report = _review("M_invalid_approval_hash")

    assert report.status is ReviewStatus.INCOMPLETE
    assert report.applied_changes == ()
    invalid = [
        item for item in report.findings if item.category is FindingCategory.INVALID_APPROVAL
    ]
    assert invalid
    assert invalid[0].severity is Severity.COVERAGE_GAP
    assert _blockers(report) == []
