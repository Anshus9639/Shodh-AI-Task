"""Conflict-aware policy verification (Phase 4 development scenarios).

A disputed approval leaves the affected scope undetermined. Differences that a
diagnostic candidate explains must not become behavioural blockers, while every
independently reproduced blocker must survive. Status follows the truth table:
a blocker wins, otherwise an undetermined mandatory scope is INCOMPLETE.
"""

from __future__ import annotations

from pathlib import Path

from verityflow.models.report import FindingCategory, Severity
from verityflow.verify.models import ReviewStatus
from verityflow.verify.review import review_bundle

ROOT = Path(__file__).resolve().parent.parent.parent
APPROVALS = ROOT / "fixtures" / "remediation" / "approvals"


def _review(name: str):
    return review_bundle(APPROVALS / name)


def _blockers(report):
    return [item for item in report.findings if item.severity is Severity.BLOCKER]


def _by_category(report, category: FindingCategory):
    return [item for item in report.findings if item.category is category]


def test_conflict_alone_is_incomplete_and_never_a_blocker() -> None:
    report = _review("I_conflicting_disputed_impl")

    assert report.status is ReviewStatus.INCOMPLETE
    assert report.applied_changes == ()
    assert _blockers(report) == []
    assert _by_category(report, FindingCategory.UNDETERMINED_CONFLICT)
    assert all(
        item.severity is Severity.COVERAGE_GAP
        for item in _by_category(report, FindingCategory.UNDETERMINED_CONFLICT)
    )
    analysis = report.conflict_analysis
    assert len(analysis) == 1
    entry = analysis[0]
    assert entry.confirmed_base == "confirmed"
    assert entry.affected_change_id == "C1@1"
    assert entry.affected_rule == "R3"
    assert entry.conflicting_approval_ids == ("A1", "A2")
    assert entry.diagnostic_variants == ("variant:C1@1",)
    assert entry.undetermined_scope
    assert entry.independent_findings == ()
    assert any("cmp|wait_time|>=|25|minutes" == item for item in entry.affected_conditions)
    assert any("cmp|wait_time|>=|30|minutes" == item for item in entry.affected_conditions)


def test_conflict_dependent_difference_is_downgraded_but_disclosed() -> None:
    report = _review("I_conflicting_disputed_impl")

    for item in _by_category(report, FindingCategory.UNDETERMINED_CONFLICT):
        assert item.rule_ref == "R3" or item.rule_ref == "R4"
        assert item.location is not None
        assert item.evidence_refs
        assert item.recommendation
        disclosure = f"{item.message} {item.explanation or ''} {item.recommendation}".lower()
        assert "approval" in disclosure or "conflict" in disclosure


def test_independent_blocker_survives_a_conflict_and_keeps_changes_required() -> None:
    report = _review("J_conflict_plus_independent_defect")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    precedence = _by_category(report, FindingCategory.PRECEDENCE)
    assert len(precedence) == 1
    assert precedence[0].severity is Severity.BLOCKER
    assert precedence[0].artifact_id == "ROUTER"
    assert precedence[0].rule_ref == "R1"

    assert _by_category(report, FindingCategory.UNDETERMINED_CONFLICT)
    assert all(
        item.severity is Severity.COVERAGE_GAP
        for item in _by_category(report, FindingCategory.UNDETERMINED_CONFLICT)
    )
    assert _by_category(report, FindingCategory.THRESHOLD_CHANGED) == []
    analysis = report.conflict_analysis[0]
    assert analysis.affected_rule == "R3"
    # The precedence blocker is outside the conflicted scope, so it is not
    # claimed as an explanation of the conflict.
    assert analysis.independent_findings == ()


def test_multiple_conflicts_produce_one_analysis_each() -> None:
    report = _review("K_multiple_conflicts")

    assert report.status is ReviewStatus.INCOMPLETE
    assert report.applied_changes == ()
    assert _blockers(report) == []
    entries = {entry.affected_change_id: entry for entry in report.conflict_analysis}
    assert set(entries) == {"C1@1", "C2@1"}
    assert entries["C1@1"].affected_rule == "R3"
    assert entries["C1@1"].conflicting_approval_ids == ("A1", "A2")
    assert entries["C2@1"].affected_rule == "R2"
    assert entries["C2@1"].conflicting_approval_ids == ("A3", "A4")
    for entry in entries.values():
        assert entry.diagnostic_variants
        assert entry.undetermined_scope
    discovered = {
        item.rule_ref for item in _by_category(report, FindingCategory.UNDETERMINED_CONFLICT)
    }
    assert {"R2", "R3"} <= discovered


def test_runtime_error_independent_of_conflict_remains_a_blocker() -> None:
    report = _review("N_conflict_plus_runtime_error")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    runtime = _by_category(report, FindingCategory.RUNTIME_ERROR)
    assert len(runtime) == 1
    assert runtime[0].severity is Severity.BLOCKER
    assert runtime[0].artifact_id == "ROUTER"
    assert runtime[0].rule_ref == "R1"
    assert _by_category(report, FindingCategory.UNDETERMINED_CONFLICT)
    assert all(
        item.severity is Severity.COVERAGE_GAP
        for item in _by_category(report, FindingCategory.UNDETERMINED_CONFLICT)
    )
    analysis = report.conflict_analysis[0]
    assert analysis.affected_change_id == "C1@1"
    assert analysis.affected_rule == "R3"
    assert analysis.conflicting_approval_ids == ("A1", "A2")


def test_conflict_cases_never_confirm_a_diagnostic_variant() -> None:
    for name in (
        "I_conflicting_disputed_impl",
        "J_conflict_plus_independent_defect",
        "K_multiple_conflicts",
        "N_conflict_plus_runtime_error",
    ):
        report = _review(name)
        assert report.confirmed_candidate_id == "confirmed"
        assert report.applied_changes == ()
        assert all(
            not entry.confirmed_base.startswith("variant:") for entry in report.conflict_analysis
        )
        assert all(
            change == "confirmed" or not change.startswith("variant:")
            for change in (report.confirmed_candidate_id,)
        )
