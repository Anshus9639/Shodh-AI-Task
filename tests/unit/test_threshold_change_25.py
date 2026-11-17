from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from verityflow.approvals.resolver import resolve_effective_requirements
from verityflow.config import Config
from verityflow.ingest.artifacts import TypedArtifacts, parse_artifacts
from verityflow.ingest.loader import load_bundle
from verityflow.ir.evaluate import CellValue, Truth, evaluate
from verityflow.ir.model import And, Cmp, Expr
from verityflow.ir.policy import ChangeState, EffectiveRequirements, Policy
from verityflow.models.manifest import ArtifactKind
from verityflow.models.report import FindingCategory, Severity
from verityflow.requirements.compile import compile_document

DEV = Path(__file__).resolve().parent.parent.parent / "fixtures" / "dev"

# Earlier conditions (missing category, urgent priority) do not match, so the
# wait-time threshold decides where this ticket routes.
WAITING_25 = {
    "category": CellValue.present("general"),
    "priority": CellValue.present("normal"),
    "wait_time": CellValue.present(Fraction(25)),
}


def _stage1(name: str) -> tuple[TypedArtifacts, EffectiveRequirements]:
    loaded = load_bundle(DEV / name)
    typed = parse_artifacts(loaded)
    assert typed.dictionary is not None
    assert typed.requirements
    base, _ = compile_document(typed.requirements[0].document)
    change_entries = loaded.manifest.entries_of_kind(ArtifactKind.CHANGE_REQUESTS)
    effective = resolve_effective_requirements(
        base,
        manifest=loaded.manifest,
        changes=typed.changes,
        dictionary=typed.dictionary,
        config=Config(),
        requirements_ref="REQ@1.0",
        changes_ref=change_entries[0].ref if change_entries else None,
    )
    return typed, effective


def _first_cmp(expr: Expr) -> Cmp:
    if isinstance(expr, Cmp):
        return expr
    if isinstance(expr, And):
        for item in expr.items:
            try:
                return _first_cmp(item)
            except LookupError:
                continue
    raise LookupError("no comparison in expression")


def _threshold(policy: Policy) -> Cmp:
    rule = policy.rule("R3")
    assert rule is not None
    return _first_cmp(rule.condition)


def _route(policy: Policy, values: dict[str, CellValue]) -> str | None:
    for rule in policy.rules:
        if evaluate(rule.condition, values) is Truth.TRUE:
            return rule.destination
    return None


def test_original_policy_routes_a_ticket_waiting_exactly_25_minutes_normally() -> None:
    _, effective = _stage1("B01_starter")
    confirmed = effective.confirmed()
    assert confirmed is not None
    assert _threshold(confirmed).value == 30
    assert _route(confirmed, WAITING_25) == "normal_queue"


def test_approved_25_minute_threshold_applies_and_reroutes() -> None:
    _, effective = _stage1("B07_approved_threshold_25")
    confirmed = effective.confirmed()
    assert confirmed is not None
    assert effective.applied == ("C1@1",)
    decision = effective.decisions[0]
    assert decision.state is ChangeState.APPROVED
    assert decision.approval_ids == ("A7",)
    threshold = _threshold(confirmed)
    assert (threshold.value, threshold.unit) == (25, "minutes")
    assert _route(confirmed, WAITING_25) == "escalation_queue"


def test_unapproved_25_minute_suggestion_leaves_30_effective() -> None:
    _, effective = _stage1("B08_unapproved_threshold_25")
    confirmed = effective.confirmed()
    assert confirmed is not None
    assert effective.applied == ()
    assert effective.decisions[0].state is ChangeState.UNAPPROVED
    assert _threshold(confirmed).value == 30
    assert _route(confirmed, WAITING_25) == "normal_queue"
    finding = next(
        item for item in effective.findings if item.category is FindingCategory.UNAPPROVED_CHANGE
    )
    assert finding.severity is Severity.INFO


def test_conflicting_25_minute_approvals_are_incomplete_and_leave_30_effective() -> None:
    _, effective = _stage1("B09_conflicting_threshold_25")
    confirmed = effective.confirmed()
    assert confirmed is not None
    assert effective.applied == ()
    assert effective.decisions[0].state is ChangeState.CONFLICT
    assert [(scope.rule_ref, scope.attribute) for scope in effective.undetermined] == [
        ("R3", "approval")
    ]
    assert _threshold(confirmed).value == 30
    assert _route(confirmed, WAITING_25) == "normal_queue"
    finding = next(
        item
        for item in effective.findings
        if item.category is FindingCategory.UNDETERMINED_CONFLICT
    )
    assert finding.severity is Severity.COVERAGE_GAP
    variants = [candidate for candidate in effective.candidates if candidate.diagnostic_only]
    assert [variant.candidate_id for variant in variants] == ["variant:C1@1"]
