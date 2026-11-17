from __future__ import annotations

from pathlib import Path

from support import run_stage1

from verityflow.ir.model import Cmp
from verityflow.ir.policy import ChangeState
from verityflow.models.report import FindingCategory, Severity

ROOT = Path(__file__).resolve().parent.parent.parent
DEV = ROOT / "fixtures" / "dev"


def test_starter_bundle_end_to_end() -> None:
    result = run_stage1(DEV / "B01_starter")
    assert [rule.rule_ref for rule in result.base_policy.rules] == ["R1", "R2", "R3", "R4"]
    assert result.effective.confirmed_candidate_id == "confirmed"
    assert result.effective.applied == ()
    assert result.effective.undetermined == ()
    assert result.mappings["spec"].complete is True
    assert result.mappings["workflow"].complete is True
    assert result.findings == ()


def test_every_rule_anchor_resolves_to_the_artifact_text() -> None:
    result = run_stage1(DEV / "B01_starter")
    index = result.loaded.index_of(result.entry.ref)
    assert index is not None
    for rule in result.base_policy.rules:
        assert index.check_span(rule.anchor, expected_path=result.entry.path) is None
        assert rule.source_text is not None
        assert rule.source_text in (rule.anchor.excerpt or "")


def test_approved_change_is_applied_and_bound_to_its_hash() -> None:
    result = run_stage1(DEV / "B03_approved_change")
    assert result.effective.applied == ("C1@1",)
    decision = result.effective.decisions[0]
    assert decision.state is ChangeState.APPROVED
    confirmed = result.effective.confirmed()
    assert confirmed is not None
    threshold = confirmed.rule("R3")
    assert threshold is not None
    assert isinstance(threshold.condition, Cmp)
    assert threshold.condition.value == 20


def test_unapproved_change_is_reported_but_grants_no_authority() -> None:
    result = run_stage1(DEV / "B05_unapproved_change")
    assert result.effective.applied == ()
    assert result.effective.decisions[0].state is ChangeState.UNAPPROVED
    assert not any(
        finding.severity in {Severity.BLOCKER, Severity.COVERAGE_GAP} for finding in result.findings
    )


def test_conflicting_approvals_produce_diagnostic_variants_only() -> None:
    result = run_stage1(DEV / "B06_conflicting_approvals")
    assert result.effective.applied == ()
    assert result.effective.decisions[0].state is ChangeState.CONFLICT
    assert result.effective.undetermined != ()
    confirmed = result.effective.confirmed()
    assert confirmed is not None and confirmed.candidate_id == "confirmed"
    variants = [candidate for candidate in result.effective.candidates if candidate != confirmed]
    assert variants and all(candidate.diagnostic_only for candidate in variants)
    assert any(
        finding.category is FindingCategory.UNDETERMINED_CONFLICT
        and finding.severity is Severity.COVERAGE_GAP
        for finding in result.findings
    )


def test_mapping_is_inferred_without_identifiers() -> None:
    result = run_stage1(DEV / "B01_starter")
    for mapped in result.mappings.values():
        assert all(match.method.value == "inferred" for match in mapped.matches)
        assert mapped.matches
