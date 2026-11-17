from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from verityflow.cli import app
from verityflow.config import Config, Limits
from verityflow.models.report import FindingCategory, Severity
from verityflow.verify.models import ComparisonMode, ReviewStatus
from verityflow.verify.review import review_bundle

ROOT = Path(__file__).resolve().parent.parent.parent
DEV = ROOT / "fixtures" / "dev"


def _review(name: str, *, config: Config | None = None):
    return review_bundle(DEV / name, config=config)


def _categories(report) -> set[FindingCategory]:
    return {finding.category for finding in report.findings}


def test_stage1_bundle_passes_without_code() -> None:
    report = _review("B01_starter")

    assert report.status is ReviewStatus.PASS
    assert report.exhaustive
    assert report.unreviewed == ()
    code_check = next(check for check in report.checks if check.name == "code")
    assert code_check.outcome.value == "skipped"
    assert {record.name for record in report.comparisons} == {"spec", "workflow"}
    assert all(record.equal for record in report.comparisons)


def test_linked_router_agreement_passes() -> None:
    report = _review("B10_router_clean")

    assert report.status is ReviewStatus.PASS
    assert report.exhaustive
    assert report.unreviewed == ()
    by_name = {record.name: record for record in report.comparisons}
    assert by_name["code"].equal
    assert by_name["code"].mode is ComparisonMode.EXHAUSTIVE
    assert by_name["code_vs_workflow"].equal
    assert all(item.covered for item in report.coverage)
    assert any(item.code_artifact == "ROUTER@1.0" for item in report.coverage)


def test_reordered_router_requires_changes_with_a_validated_witness() -> None:
    report = _review("B11_router_precedence")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    assert FindingCategory.PRECEDENCE in _categories(report)
    record = next(item for item in report.comparisons if item.name == "code")
    assert not record.equal
    assert record.counterexamples
    witness = record.counterexamples[0]
    assert witness.validated
    assert witness.inputs["category"] is None
    assert witness.inputs["priority"] == "urgent"
    assert witness.requirement_outcome == "destination 'manual_review'"
    assert witness.artifact_outcome == "destination 'priority_queue'"


def test_strict_boundary_requires_changes_and_names_the_threshold() -> None:
    report = _review("B12_router_threshold")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    assert FindingCategory.THRESHOLD_CHANGED in _categories(report)
    record = next(item for item in report.comparisons if item.name == "code")
    validated = [item for item in record.counterexamples if item.validated]
    assert validated
    assert any(item.inputs.get("wait_time") == 30 for item in validated)


def test_runtime_error_is_a_blocker_not_agreement() -> None:
    report = _review("B13_router_runtime")

    assert report.status is ReviewStatus.CHANGES_REQUIRED
    blockers = [
        finding for finding in report.findings if finding.category is FindingCategory.RUNTIME_ERROR
    ]
    assert blockers
    assert all(finding.severity is Severity.BLOCKER for finding in blockers)
    record = next(item for item in report.comparisons if item.name == "code")
    assert not record.equal
    assert any(item.inputs["category"] is None for item in record.counterexamples)


def test_sampled_space_is_incomplete_and_disclosed() -> None:
    config = Config(limits=Limits(max_cells=8))
    report = _review("B10_router_clean", config=config)

    assert report.status is ReviewStatus.INCOMPLETE
    assert not report.exhaustive
    assert all(record.mode is ComparisonMode.SAMPLED for record in report.comparisons)
    assert any(scope.kind == "sampling" for scope in report.unreviewed)
    assert any(
        check.name == "sampling" and check.outcome.value == "coverage_gap"
        for check in report.checks
    )
    assert "combinations were not evaluated" in report.detail


def test_cli_exit_codes_follow_the_review_status(tmp_path: Path) -> None:
    runner = CliRunner()
    passing = runner.invoke(app, ["review", str(DEV / "B01_starter")])
    assert passing.exit_code == 0
    assert json.loads(passing.stdout)["status"] == "PASS"

    failing = runner.invoke(app, ["review", str(DEV / "B11_router_precedence")])
    assert failing.exit_code == 1
    assert json.loads(failing.stdout)["status"] == "CHANGES_REQUIRED"

    missing = runner.invoke(app, ["review", str(tmp_path / "absent")])
    assert missing.exit_code == 2
