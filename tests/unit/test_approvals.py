from __future__ import annotations

from pathlib import Path

from verityflow.approvals.resolver import resolve_effective_requirements
from verityflow.config import Config, Limits
from verityflow.ingest.artifacts import TypedArtifacts, parse_artifacts
from verityflow.ingest.loader import load_bundle
from verityflow.ir.model import And, Cmp, Expr
from verityflow.ir.policy import ChangeState, EffectiveRequirements
from verityflow.models.changes import ChangeArtifact, ChangePayload
from verityflow.models.manifest import BundleManifest
from verityflow.models.report import FindingCategory, Severity
from verityflow.requirements.compile import compile_document

DEV = Path(__file__).resolve().parent.parent.parent / "fixtures" / "dev"
STARTER = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, waiting >= 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
"""


def _load(name: str) -> tuple[BundleManifest, TypedArtifacts]:
    loaded = load_bundle(DEV / name)
    return loaded.manifest, parse_artifacts(loaded)


def _bind(manifest: BundleManifest, changes: ChangeArtifact) -> BundleManifest:
    hashes = {
        (change.change_id, change.change_version): change.content_hash()
        for change in changes.changes
    }
    records = tuple(
        record.model_copy(
            update={"change_sha256": hashes[(record.change_id, record.change_version)]}
        )
        if (record.change_id, record.change_version) in hashes
        else record
        for record in manifest.approvals
    )
    return manifest.model_copy(update={"approvals": records})


def _resolve(
    name: str,
    *,
    manifest_edit=None,
    changes_edit=None,
    rebind: bool = True,
    config: Config | None = None,
    text: str = STARTER,
) -> EffectiveRequirements:
    manifest, typed = _load(name)
    assert typed.dictionary is not None
    changes = changes_edit(typed.changes) if changes_edit is not None else typed.changes
    if manifest_edit is not None:
        manifest = manifest_edit(manifest)
    if rebind:
        manifest = _bind(manifest, changes)
    document = parse_artifacts_document(text, typed)
    base, _ = compile_document(document)
    return resolve_effective_requirements(
        base,
        manifest=manifest,
        changes=changes,
        dictionary=typed.dictionary,
        config=config or Config(),
        requirements_ref="REQ@1.0",
        changes_ref="CHG@1.0",
    )


def parse_artifacts_document(text: str, typed: TypedArtifacts):
    from verityflow.requirements.parser import parse_requirements

    assert typed.dictionary is not None
    return parse_requirements(text, "requirements.txt", typed.dictionary)


def _categories(effective: EffectiveRequirements) -> set[FindingCategory]:
    return {finding.category for finding in effective.findings}


def _decision(effective: EffectiveRequirements, change_id: str = "C1"):
    return next(item for item in effective.decisions if item.change_id == change_id)


def _first_cmp(expr: Expr) -> Cmp:
    if isinstance(expr, Cmp):
        return expr
    if isinstance(expr, (And,)):
        for item in expr.items:
            try:
                return _first_cmp(item)
            except LookupError:
                continue
    raise LookupError("no comparison in expression")


def test_bundle_without_changes_confirms_the_base_policy() -> None:
    effective = _resolve("B01_starter")
    confirmed = effective.confirmed()
    assert confirmed is not None
    assert confirmed.candidate_id == "confirmed"
    assert effective.decisions == ()
    assert effective.applied == ()
    assert effective.findings == ()
    assert confirmed.rules[2].rule_ref == "R3"


def test_valid_approval_applies_the_threshold() -> None:
    effective = _resolve("B03_approved_change")
    confirmed = effective.confirmed()
    assert confirmed is not None
    assert effective.applied == ("C1@1",)
    decision = _decision(effective)
    assert decision.state is ChangeState.APPROVED
    assert decision.approval_ids == ("A1",)
    rule = confirmed.rule("R3")
    assert rule is not None
    assert _first_cmp(rule.condition).value == 20
    assert _first_cmp(rule.condition).unit == "minutes"


def test_unapproved_change_is_reported_but_not_applied() -> None:
    effective = _resolve("B05_unapproved_change")
    confirmed = effective.confirmed()
    assert confirmed is not None
    assert effective.applied == ()
    assert _decision(effective).state is ChangeState.UNAPPROVED
    finding = next(
        item for item in effective.findings if item.category is FindingCategory.UNAPPROVED_CHANGE
    )
    assert finding.severity is Severity.INFO
    rule = confirmed.rule("R3")
    assert rule is not None
    assert _first_cmp(rule.condition).value == 30


def test_tampered_change_payload_invalidates_the_approval() -> None:
    def tamper(changes: ChangeArtifact) -> ChangeArtifact:
        change = changes.changes[0].model_copy(
            update={"to_payload": ChangePayload(value=5, unit="minutes")}
        )
        return changes.model_copy(update={"changes": (change,)})

    effective = _resolve("B03_approved_change", changes_edit=tamper, rebind=False)
    assert effective.applied == ()
    assert _decision(effective).state is ChangeState.UNAPPROVED
    assert FindingCategory.INVALID_APPROVAL in _categories(effective)


def test_tampered_hash_is_an_invalid_approval() -> None:
    def tamper(manifest: BundleManifest) -> BundleManifest:
        records = tuple(
            record.model_copy(update={"change_sha256": "0" * 64}) for record in manifest.approvals
        )
        return manifest.model_copy(update={"approvals": records})

    effective = _resolve("B03_approved_change", manifest_edit=tamper, rebind=False)
    assert effective.applied == ()
    finding = next(
        item for item in effective.findings if item.category is FindingCategory.INVALID_APPROVAL
    )
    assert finding.severity is Severity.COVERAGE_GAP


def test_unauthorized_role_has_no_authority() -> None:
    def demote(manifest: BundleManifest) -> BundleManifest:
        records = tuple(
            record.model_copy(update={"approver_role": "intern"}) for record in manifest.approvals
        )
        return manifest.model_copy(update={"approvals": records})

    effective = _resolve("B03_approved_change", manifest_edit=demote)
    assert effective.applied == ()
    assert _decision(effective).state is ChangeState.UNAPPROVED
    ignored = [
        item for item in effective.findings if item.category is FindingCategory.APPROVAL_IGNORED
    ]
    assert ignored and ignored[0].severity is Severity.INFO


def test_rejection_does_not_apply() -> None:
    def reject(manifest: BundleManifest) -> BundleManifest:
        records = tuple(
            record.model_copy(update={"decision": "reject"}) for record in manifest.approvals
        )
        return manifest.model_copy(update={"approvals": records})

    effective = _resolve("B03_approved_change", manifest_edit=reject)
    assert effective.applied == ()
    assert _decision(effective).state is ChangeState.REJECTED


def test_approve_and_reject_conflict_leaves_scope_undetermined() -> None:
    effective = _resolve("B06_conflicting_approvals")
    confirmed = effective.confirmed()
    assert confirmed is not None
    assert effective.applied == ()
    assert _decision(effective).state is ChangeState.CONFLICT
    assert [(scope.rule_ref, scope.attribute) for scope in effective.undetermined] == [
        ("R3", "approval")
    ]
    variants = [candidate for candidate in effective.candidates if candidate.diagnostic_only]
    assert [variant.candidate_id for variant in variants] == ["variant:C1@1"]
    assert FindingCategory.UNDETERMINED_CONFLICT in _categories(effective)


def test_supersession_resolves_the_conflict() -> None:
    def supersede(manifest: BundleManifest) -> BundleManifest:
        records = tuple(
            record.model_copy(update={"supersedes": ("A3",)})
            if record.approval_id == "A2"
            else record
            for record in manifest.approvals
        )
        return manifest.model_copy(update={"approvals": records})

    effective = _resolve("B06_conflicting_approvals", manifest_edit=supersede)
    assert _decision(effective).state is ChangeState.APPROVED
    assert effective.applied == ("C1@1",)


def test_supersession_cycle_is_ignored_with_a_warning() -> None:
    def cycle(manifest: BundleManifest) -> BundleManifest:
        records = tuple(
            record.model_copy(
                update={"supersedes": (("A3",) if record.approval_id == "A2" else ("A2",))}
            )
            for record in manifest.approvals
        )
        return manifest.model_copy(update={"approvals": records})

    effective = _resolve("B06_conflicting_approvals", manifest_edit=cycle)
    warnings = [
        item for item in effective.findings if item.category is FindingCategory.APPROVAL_IGNORED
    ]
    assert any(item.severity is Severity.WARNING for item in warnings)
    assert _decision(effective).state is ChangeState.CONFLICT


def test_stale_base_version_is_reported() -> None:
    def stale(changes: ChangeArtifact) -> ChangeArtifact:
        change = changes.changes[0].model_copy(update={"base_version": "0.9"})
        return changes.model_copy(update={"changes": (change,)})

    effective = _resolve("B03_approved_change", changes_edit=stale)
    assert effective.applied == ()
    assert _decision(effective).state is ChangeState.STALE
    assert FindingCategory.STALE_CHANGE in _categories(effective)


def test_unsupported_threshold_without_a_unit() -> None:
    def no_unit(changes: ChangeArtifact) -> ChangeArtifact:
        change = changes.changes[0].model_copy(update={"to_payload": ChangePayload(value=20)})
        return changes.model_copy(update={"changes": (change,)})

    effective = _resolve("B03_approved_change", changes_edit=no_unit)
    assert effective.applied == ()
    assert _decision(effective).state is ChangeState.UNSUPPORTED
    assert FindingCategory.UNSUPPORTED_CHANGE_OPERATION in _categories(effective)


def test_threshold_change_propagates_into_an_exception() -> None:
    text = (
        "R1. Missing category -> manual review.\n"
        "R2. Otherwise, urgent ticket -> priority queue.\n"
        "R3. Otherwise, waiting >= 30 minutes -> escalation queue.\n"
        "Exception to R3: if vip, route to priority queue.\n"
        "R4. All remaining tickets -> normal queue.\n"
    )
    effective = _resolve("B03_approved_change", text=text)
    confirmed = effective.confirmed()
    assert confirmed is not None
    assert effective.applied == ("C1@1",)
    exception = confirmed.rule("R3.exception")
    assert exception is not None
    comparison = _first_cmp(exception.condition)
    assert comparison.value == 20
    assert comparison.field == "wait_time"


def test_candidate_cap_keeps_conflicts_undetermined() -> None:
    effective = _resolve(
        "B06_conflicting_approvals",
        config=Config(limits=Limits(max_candidate_policies=1)),
    )
    assert [candidate.candidate_id for candidate in effective.candidates] == ["confirmed"]
    assert FindingCategory.UNDETERMINED_CONFLICT in _categories(effective)


def test_policy_with_exception_keeps_dense_precedence() -> None:
    text = (
        "R1. waiting >= 30 minutes -> escalation queue.\n"
        "Exception to R1: if vip, route to priority queue.\n"
        "R2. All remaining tickets -> normal queue.\n"
    )
    effective = _resolve("B01_starter", text=text)
    confirmed = effective.confirmed()
    assert confirmed is not None
    assert [rule.precedence for rule in confirmed.rules] == [0, 1, 2]
