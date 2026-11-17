"""Deterministic Stage 2 review orchestration.

``review_bundle`` loads one bundle, resolves approvals into an effective
requirement policy, compiles every declared implementation into the shared
routing contract, and compares them over one disclosed ticket space. It never
executes bundle Python (see :mod:`verityflow.verify.python_ast`) and never trusts
rule IDs as evidence.

The deterministic result is a :class:`ReviewReport`: status, per-artifact checks,
comparison records with validated counterexamples, requirement coverage, every
disclosed unreviewed region, and the consolidated findings.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from verityflow.approvals.resolver import resolve_effective_requirements
from verityflow.config import Config
from verityflow.ingest.artifacts import TypedArtifacts, parse_artifacts
from verityflow.ingest.loader import LoadedBundle, load_bundle
from verityflow.ir.model import Expr
from verityflow.ir.policy import EffectiveRequirements, Policy
from verityflow.models.common import ArtifactRef
from verityflow.models.dictionary import DataDictionary
from verityflow.models.manifest import ArtifactEntry, ArtifactKind, BundleManifest
from verityflow.models.report import (
    CheckOutcome,
    CheckResult,
    Finding,
    FindingCategory,
    Severity,
    UnreviewedScope,
    make_finding,
)
from verityflow.requirements.compile import compile_document
from verityflow.verify.code import verify_code
from verityflow.verify.coverage import analyze_coverage
from verityflow.verify.domain import TicketSpace, build_ticket_space
from verityflow.verify.interactions import analyze_interactions
from verityflow.verify.models import (
    ComparisonMode,
    ComparisonRecord,
    RequirementCoverage,
    ReviewReport,
    ReviewStatus,
)
from verityflow.verify.python_ast import (
    CodeProgram,
    RouterLimitError,
    RouterSyntaxError,
    RouterUnsupportedError,
    compile_router,
)
from verityflow.verify.routing import Router, SpecRouter, build_spec_router
from verityflow.verify.spec import verify_spec
from verityflow.verify.workflow import (
    WorkflowRouter,
    build_workflow_router,
    verify_workflow,
)

_BUNDLE = "bundle.json"


@dataclass(frozen=True, slots=True)
class _ArtifactVerification:
    space: TicketSpace
    findings: list[Finding]
    comparisons: list[ComparisonRecord]
    checks: list[CheckResult]
    unreviewed: list[UnreviewedScope]
    spec_router: Router | None
    workflow_router: Router | None
    code_programs: tuple[CodeProgram, ...]


def review_bundle(
    bundle: Path,
    *,
    config: Config | None = None,
    requirements_ref: str | None = None,
) -> ReviewReport:
    active = config or Config()
    loaded = load_bundle(bundle, limits=active.limits)
    return review_loaded(loaded, config=active, requirements_ref=requirements_ref)


def review_loaded(
    loaded: LoadedBundle,
    *,
    config: Config | None = None,
    requirements_ref: str | None = None,
) -> ReviewReport:
    active = config or Config()
    manifest = loaded.manifest
    typed = parse_artifacts(loaded)
    findings: list[Finding] = [*loaded.findings, *typed.findings]
    unreviewed: list[UnreviewedScope] = [
        UnreviewedScope(kind="provenance", description=description)
        for description in loaded.unreviewed
    ]
    dictionary = typed.dictionary

    if dictionary is None:
        return _report(
            manifest,
            confirmed=None,
            comparisons=[],
            coverage=(),
            findings=findings,
            checks=[CheckResult(name="bundle", outcome=CheckOutcome.COVERAGE_GAP)],
            unreviewed=unreviewed,
            status=ReviewStatus.INCOMPLETE,
            exhaustive=False,
            detail=(
                "no valid data dictionary was loaded, so requirements and artifacts "
                "cannot be compared"
            ),
        )

    entry = _requirements_entry(manifest, requirements_ref, findings)
    if entry is None:
        return _report(
            manifest,
            confirmed=None,
            comparisons=[],
            coverage=(),
            findings=findings,
            checks=[CheckResult(name="requirements", outcome=CheckOutcome.COVERAGE_GAP)],
            unreviewed=unreviewed,
            status=ReviewStatus.INCOMPLETE,
            exhaustive=False,
            detail="the review target requirement artifact is not available",
        )
    parsed = next((item for item in typed.requirements if item.entry.ref == entry.ref), None)
    if parsed is None:
        findings.append(
            make_finding(
                FindingCategory.PROVENANCE_FAILURE,
                Severity.COVERAGE_GAP,
                f"requirement artifact {entry.ref} was not loaded; nothing can be reviewed",
                artifact_id=entry.artifact_id,
                artifact_version=entry.version,
            )
        )
        return _report(
            manifest,
            confirmed=None,
            comparisons=[],
            coverage=(),
            findings=findings,
            checks=[CheckResult(name="requirements", outcome=CheckOutcome.COVERAGE_GAP)],
            unreviewed=unreviewed,
            status=ReviewStatus.INCOMPLETE,
            exhaustive=False,
            detail=f"requirement artifact {entry.ref} was not loaded",
        )

    requirements_artifact = ArtifactRef(artifact_id=entry.artifact_id, version=entry.version)
    base_policy, compile_findings = compile_document(
        parsed.document, artifact=requirements_artifact
    )
    findings.extend(compile_findings)
    effective = resolve_effective_requirements(
        base_policy,
        manifest=manifest,
        changes=typed.changes,
        dictionary=dictionary,
        config=active,
        requirements_ref=entry.ref,
        changes_ref=_first_ref(manifest, ArtifactKind.CHANGE_REQUESTS),
    )
    findings.extend(effective.findings)
    confirmed = effective.confirmed()
    policy = confirmed if confirmed is not None else base_policy
    if confirmed is None:
        unreviewed.append(
            UnreviewedScope(
                kind="approvals",
                description=(
                    "no single effective requirement policy could be confirmed; the base "
                    "policy is used only as a diagnostic reference"
                ),
            )
        )

    verification = _verify_artifacts(
        loaded,
        manifest,
        typed,
        dictionary,
        policy,
        base_policy,
        requirements_artifact=requirements_artifact,
        config=active,
    )
    findings.extend(verification.findings)
    unreviewed.extend(verification.unreviewed)

    interaction = analyze_interactions(policy, verification.space)
    findings.extend(interaction.findings)
    if interaction.unreachable_rules:
        unreviewed.append(
            UnreviewedScope(
                kind="interaction",
                description=(
                    (
                        "requirement rules that never fire have no observable behaviour: "
                        if verification.space.exhaustive
                        else "requirement rules not exercised by the sampled ticket space: "
                    )
                    + ", ".join(interaction.unreachable_rules)
                ),
                rule_refs=interaction.unreachable_rules,
            )
        )

    coverage = analyze_coverage(
        policy,
        verification.space,
        spec=verification.spec_router,
        workflow=verification.workflow_router,
        code=verification.code_programs[0] if verification.code_programs else None,
    )
    unreviewed.extend(coverage.unreviewed)
    if not verification.space.exhaustive:
        unreviewed.append(UnreviewedScope(kind="sampling", description=verification.space.detail))

    checks = [
        _check(
            "requirements",
            effective.findings,
            detail=(
                f"confirmed candidate {effective.confirmed_candidate_id!r}"
                if confirmed is not None
                else "no single effective requirement policy was confirmed"
            ),
        ),
        *verification.checks,
        _check(
            "interactions",
            interaction.findings,
            detail="requirement rules that never fire are disclosed",
        ),
        CheckResult(
            name="coverage",
            outcome=(CheckOutcome.PASS if not coverage.uncovered else CheckOutcome.COVERAGE_GAP),
            detail=(
                "every requirement rule was exercised by an evaluated ticket"
                if not coverage.uncovered
                else "requirements with no evaluated witness: " + ", ".join(coverage.uncovered)
            ),
        ),
        CheckResult(
            name="sampling",
            outcome=(
                CheckOutcome.PASS if verification.space.exhaustive else CheckOutcome.COVERAGE_GAP
            ),
            detail=verification.space.detail,
        ),
    ]

    blocker = any(finding.severity is Severity.BLOCKER for finding in findings)
    gap = any(finding.severity is Severity.COVERAGE_GAP for finding in findings)
    exhaustive = (
        verification.space.exhaustive
        and confirmed is not None
        and all(record.mode is ComparisonMode.EXHAUSTIVE for record in verification.comparisons)
    )
    if blocker:
        status = ReviewStatus.CHANGES_REQUIRED
    elif gap or not exhaustive or unreviewed:
        status = ReviewStatus.INCOMPLETE
    else:
        status = ReviewStatus.PASS

    return _report(
        manifest,
        confirmed=confirmed,
        comparisons=verification.comparisons,
        coverage=coverage.coverage,
        findings=findings,
        checks=checks,
        unreviewed=unreviewed,
        status=status,
        exhaustive=exhaustive,
        detail=_summary(status, verification.space, verification.comparisons, coverage.uncovered),
        effective=effective,
    )


def _verify_artifacts(
    loaded: LoadedBundle,
    manifest: BundleManifest,
    typed: TypedArtifacts,
    dictionary: DataDictionary,
    policy: Policy,
    base_policy: Policy,
    *,
    requirements_artifact: ArtifactRef,
    config: Config,
) -> _ArtifactVerification:
    findings: list[Finding] = []
    comparisons: list[ComparisonRecord] = []
    checks: list[CheckResult] = []
    unreviewed: list[UnreviewedScope] = []
    policy_ref = str(requirements_artifact)

    spec_router: SpecRouter | None = None
    spec_problem = _target_problem(manifest, ArtifactKind.SPEC, manifest.review_target.spec)
    if spec_problem is not None:
        findings.append(_target_finding(manifest, spec_problem))
        unreviewed.append(
            UnreviewedScope(
                kind="artifact", description=spec_problem, artifact=manifest.review_target.spec
            )
        )
    elif manifest.review_target.spec is not None and typed.spec is not None:
        spec_entry = _entry(manifest, manifest.review_target.spec)
        if spec_entry is None:
            raise AssertionError("review target spec was validated by the manifest")
        spec_artifact = ArtifactRef(artifact_id=spec_entry.artifact_id, version=spec_entry.version)
        program = build_spec_router(typed.spec, dictionary, spec_artifact)
        findings.extend(program.findings)
        spec_router = program.router
        if program.router.problem is not None:
            unreviewed.append(
                UnreviewedScope(
                    kind="artifact",
                    description=program.router.problem,
                    artifact=spec_entry.ref,
                )
            )
    elif manifest.review_target.spec is not None:
        unreviewed.append(
            UnreviewedScope(
                kind="artifact",
                description=(
                    f"spec artifact {manifest.review_target.spec} could not be parsed; "
                    "the specification is unreviewed"
                ),
                artifact=manifest.review_target.spec,
            )
        )

    workflow_router: WorkflowRouter | None = None
    workflow_problem = _target_problem(
        manifest, ArtifactKind.WORKFLOW, manifest.review_target.workflow
    )
    if workflow_problem is not None:
        findings.append(_target_finding(manifest, workflow_problem))
        unreviewed.append(
            UnreviewedScope(
                kind="artifact",
                description=workflow_problem,
                artifact=manifest.review_target.workflow,
            )
        )
    elif manifest.review_target.workflow is not None and typed.workflow is not None:
        workflow_entry = _entry(manifest, manifest.review_target.workflow)
        if workflow_entry is None:
            raise AssertionError("review target workflow was validated by the manifest")
        workflow_artifact = ArtifactRef(
            artifact_id=workflow_entry.artifact_id, version=workflow_entry.version
        )
        workflow_program = build_workflow_router(
            typed.workflow,
            dictionary,
            workflow_artifact,
            max_steps=config.limits.interpreter_max_steps,
        )
        findings.extend(workflow_program.findings)
        workflow_router = workflow_program.router
        if workflow_program.router.problem is not None:
            unreviewed.append(
                UnreviewedScope(
                    kind="artifact",
                    description=workflow_program.router.problem,
                    artifact=workflow_entry.ref,
                )
            )
    elif manifest.review_target.workflow is not None:
        unreviewed.append(
            UnreviewedScope(
                kind="artifact",
                description=(
                    f"workflow artifact {manifest.review_target.workflow} could not be "
                    "parsed; the workflow is unreviewed"
                ),
                artifact=manifest.review_target.workflow,
            )
        )

    code_programs: list[CodeProgram] = []
    code_failures = 0
    for ref in manifest.review_target.code:
        entry = manifest.entry(ref)
        if entry is None or entry.kind is not ArtifactKind.PYTHON:
            findings.append(
                make_finding(
                    FindingCategory.STRUCTURAL,
                    Severity.COVERAGE_GAP,
                    f"review target code {ref!r} is not declared as a python artifact",
                    artifact_id=_BUNDLE,
                    artifact_version=manifest.bundle_version,
                )
            )
            code_failures += 1
            continue
        source = loaded.text_of(ref)
        if source is None:
            findings.append(
                make_finding(
                    FindingCategory.PROVENANCE_FAILURE,
                    Severity.COVERAGE_GAP,
                    f"router artifact {ref} was not loaded",
                    artifact_id=entry.artifact_id,
                    artifact_version=entry.version,
                )
            )
            code_failures += 1
            continue
        if entry.link is None:
            raise AssertionError("python artifacts require link metadata")
        try:
            code_programs.append(
                compile_router(
                    source,
                    link=entry.link,
                    dictionary=dictionary,
                    artifact=ArtifactRef(artifact_id=entry.artifact_id, version=entry.version),
                    limits=config.limits,
                    path=entry.path,
                )
            )
        except (RouterSyntaxError, RouterUnsupportedError, RouterLimitError) as exc:
            code_failures += 1
            category = (
                FindingCategory.UNSUPPORTED_CODE
                if isinstance(exc, RouterUnsupportedError)
                else FindingCategory.STRUCTURAL
            )
            findings.append(
                make_finding(
                    category,
                    Severity.COVERAGE_GAP,
                    f"router artifact {ref} cannot be reviewed: {exc}",
                    artifact_id=entry.artifact_id,
                    artifact_version=entry.version,
                )
            )
            unreviewed.append(UnreviewedScope(kind="artifact", description=str(exc), artifact=ref))

    linked_code = set(manifest.review_target.code)
    for entry in manifest.entries_of_kind(ArtifactKind.PYTHON):
        if entry.ref in linked_code:
            continue
        findings.append(
            make_finding(
                FindingCategory.DEFERRED_DESCRIPTOR,
                Severity.COVERAGE_GAP,
                f"python artifact {entry.ref} is not linked in the review target; its "
                "correspondence to the requirements is unreviewed",
                artifact_id=entry.artifact_id,
                artifact_version=entry.version,
            )
        )

    expressions: list[Expr] = [rule.condition for rule in policy.rules]
    expressions.extend(rule.condition for rule in base_policy.rules)
    if spec_router is not None:
        expressions.extend(entry.condition for entry in spec_router.entries)
    if workflow_router is not None:
        for node in workflow_router.nodes.values():
            for branch in node.branches:
                if branch.condition is not None:
                    expressions.append(branch.condition)
    extra_cuts: dict[str, tuple[Fraction, ...]] = {}
    extra_literals: dict[str, tuple[str | bool, ...]] = {}
    for code_program in code_programs:
        hints = code_program.domain_hints()
        for cut_name, cut_values in hints.cuts.items():
            extra_cuts[cut_name] = tuple(
                sorted(set(extra_cuts.get(cut_name, ())) | set(cut_values))
            )
        for literal_name, literal_values in hints.literals.items():
            extra_literals[literal_name] = tuple(
                sorted(
                    set(extra_literals.get(literal_name, ())) | set(literal_values),
                    key=repr,
                )
            )
    space = build_ticket_space(
        dictionary,
        expressions,
        max_cells=config.limits.max_cells,
        extra_cuts=extra_cuts,
        extra_literals=extra_literals,
    )

    if spec_router is not None and typed.spec is not None:
        entry = _entry(manifest, manifest.review_target.spec)
        if entry is None:
            raise AssertionError("review target spec was validated by the manifest")
        result = verify_spec(
            policy,
            typed.spec,
            dictionary,
            space,
            artifact=ArtifactRef(artifact_id=entry.artifact_id, version=entry.version),
            policy_ref=policy_ref,
            max_signature_cells=config.limits.mapping_max_signature_cells,
        )
        findings.extend(result.findings)
        comparisons.append(result.record)
        checks.append(_comparison_check("spec", result.record, result.findings))
    elif manifest.review_target.spec is None:
        checks.append(
            CheckResult(
                name="spec",
                required=False,
                outcome=CheckOutcome.SKIPPED,
                detail="no spec artifact is linked in the review target",
            )
        )
    else:
        checks.append(
            CheckResult(name="spec", outcome=CheckOutcome.COVERAGE_GAP, detail="spec unreviewed")
        )

    if workflow_router is not None and typed.workflow is not None:
        entry = _entry(manifest, manifest.review_target.workflow)
        if entry is None:
            raise AssertionError("review target workflow was validated by the manifest")
        workflow_result = verify_workflow(
            policy,
            typed.workflow,
            dictionary,
            space,
            artifact=ArtifactRef(artifact_id=entry.artifact_id, version=entry.version),
            policy_ref=policy_ref,
            max_steps=config.limits.interpreter_max_steps,
            max_signature_cells=config.limits.mapping_max_signature_cells,
        )
        findings.extend(workflow_result.findings)
        comparisons.append(workflow_result.record)
        checks.append(
            _comparison_check("workflow", workflow_result.record, workflow_result.findings)
        )
    elif manifest.review_target.workflow is None:
        checks.append(
            CheckResult(
                name="workflow",
                required=False,
                outcome=CheckOutcome.SKIPPED,
                detail="no workflow artifact is linked in the review target",
            )
        )
    else:
        checks.append(
            CheckResult(
                name="workflow",
                outcome=CheckOutcome.COVERAGE_GAP,
                detail="workflow unreviewed",
            )
        )

    workflow_reference = (
        workflow_router.artifact_ref
        if workflow_router is not None and workflow_router.problem is None
        else None
    )
    code_findings: list[Finding] = []
    for code_program in code_programs:
        code_result = verify_code(
            policy,
            code_program,
            dictionary,
            space,
            policy_ref=policy_ref,
            max_signature_cells=config.limits.mapping_max_signature_cells,
            workflow=workflow_router if workflow_reference is not None else None,
            workflow_ref=workflow_reference,
        )
        code_findings.extend(code_result.findings)
        comparisons.append(code_result.record)
        if code_result.workflow_agreement is not None:
            comparisons.append(code_result.workflow_agreement)
    findings.extend(code_findings)
    if manifest.review_target.code:
        code_records = [record for record in comparisons if record.name == "code"]
        if code_failures == len(manifest.review_target.code):
            checks.append(
                CheckResult(
                    name="code",
                    outcome=CheckOutcome.COVERAGE_GAP,
                    detail="no linked router could be compiled and reviewed",
                )
            )
        else:
            checks.append(
                _comparison_check("code", code_records[0], code_findings)
                if code_records
                else CheckResult(
                    name="code", outcome=CheckOutcome.COVERAGE_GAP, detail="code unreviewed"
                )
            )
    else:
        checks.append(
            CheckResult(
                name="code",
                required=False,
                outcome=CheckOutcome.SKIPPED,
                detail="no python router is linked in the review target",
            )
        )

    return _ArtifactVerification(
        space=space,
        findings=findings,
        comparisons=comparisons,
        checks=checks,
        unreviewed=unreviewed,
        spec_router=spec_router,
        workflow_router=workflow_router,
        code_programs=tuple(code_programs),
    )


def _target_problem(manifest: BundleManifest, kind: ArtifactKind, ref: str | None) -> str | None:
    if ref is None:
        return None
    entry = manifest.entry(ref)
    if entry is None or entry.kind is not kind:
        return f"review target {ref!r} is not declared as a {kind.value} artifact"
    declared = manifest.entries_of_kind(kind)
    if not declared or declared[0].ref != ref:
        return (
            f"review target {ref!r} is not the first declared {kind.value} artifact; "
            "only that artifact is parsed, so this review target is unreviewed"
        )
    return None


def _target_finding(manifest: BundleManifest, message: str) -> Finding:
    return make_finding(
        FindingCategory.STRUCTURAL,
        Severity.COVERAGE_GAP,
        message,
        artifact_id=_BUNDLE,
        artifact_version=manifest.bundle_version,
    )


def _entry(manifest: BundleManifest, ref: str | None) -> ArtifactEntry | None:
    if ref is None:
        return None
    return manifest.entry(ref)


def _requirements_entry(
    manifest: BundleManifest, requested: str | None, findings: list[Finding]
) -> ArtifactEntry | None:
    ref = requested or manifest.review_target.requirements[0]
    entry = manifest.entry(ref)
    if entry is None or entry.kind is not ArtifactKind.REQUIREMENTS:
        findings.append(
            make_finding(
                FindingCategory.STRUCTURAL,
                Severity.COVERAGE_GAP,
                f"review target requirement {ref!r} is not declared as a requirement artifact",
                artifact_id=_BUNDLE,
                artifact_version=manifest.bundle_version,
            )
        )
        return None
    return entry


def _first_ref(manifest: BundleManifest, kind: ArtifactKind) -> str | None:
    entries = manifest.entries_of_kind(kind)
    return entries[0].ref if entries else None


def _comparison_check(
    name: str, record: ComparisonRecord, findings: Iterable[Finding]
) -> CheckResult:
    scoped = list(findings)
    if any(finding.severity is Severity.BLOCKER for finding in scoped):
        outcome = CheckOutcome.FAILED_WITH_BLOCKER
    elif record.mode is ComparisonMode.UNREVIEWED:
        outcome = CheckOutcome.COVERAGE_GAP
    elif not record.equal:
        outcome = CheckOutcome.FAILED
    elif any(finding.severity is Severity.COVERAGE_GAP for finding in scoped):
        outcome = CheckOutcome.COVERAGE_GAP
    else:
        outcome = CheckOutcome.PASS
    return CheckResult(name=name, outcome=outcome, detail=record.detail)


def _check(name: str, findings: Iterable[Finding], *, detail: str | None = None) -> CheckResult:
    scoped = list(findings)
    if any(finding.severity is Severity.BLOCKER for finding in scoped):
        outcome = CheckOutcome.FAILED_WITH_BLOCKER
    elif any(finding.severity is Severity.COVERAGE_GAP for finding in scoped):
        outcome = CheckOutcome.COVERAGE_GAP
    elif scoped:
        outcome = CheckOutcome.FAILED
    else:
        outcome = CheckOutcome.PASS
    return CheckResult(name=name, outcome=outcome, detail=detail)


def _report(
    manifest: BundleManifest,
    *,
    confirmed: Policy | None,
    comparisons: list[ComparisonRecord],
    coverage: tuple[RequirementCoverage, ...],
    findings: list[Finding],
    checks: list[CheckResult],
    unreviewed: list[UnreviewedScope],
    status: ReviewStatus,
    exhaustive: bool,
    detail: str,
    effective: EffectiveRequirements | None = None,
) -> ReviewReport:
    return ReviewReport(
        bundle_id=manifest.bundle_id,
        bundle_version=manifest.bundle_version,
        status=status,
        requirements_ref=None if effective is None else effective.requirements_ref,
        confirmed_candidate_id=(None if effective is None else effective.confirmed_candidate_id),
        applied_changes=() if effective is None else effective.applied,
        checks=tuple(checks),
        comparisons=tuple(comparisons),
        coverage=tuple(coverage),
        findings=tuple(_dedupe(findings)),
        unreviewed=tuple(unreviewed),
        exhaustive=exhaustive,
        detail=detail,
    )


def _dedupe(findings: Iterable[Finding]) -> list[Finding]:
    seen: set[str] = set()
    unique: list[Finding] = []
    for finding in findings:
        if finding.finding_id in seen:
            continue
        seen.add(finding.finding_id)
        unique.append(finding)
    return unique


def _summary(
    status: ReviewStatus,
    space: TicketSpace,
    comparisons: list[ComparisonRecord],
    coverage_uncovered: tuple[str, ...],
) -> str:
    parts = [
        f"{status.value}: {space.checked} of {space.combinations_total} representative "
        "ticket combinations evaluated"
    ]
    if not space.exhaustive:
        parts.append(f"{space.combinations_skipped} combinations were not evaluated")
    differing = [record.name for record in comparisons if not record.equal]
    if differing:
        parts.append("comparisons with differences: " + ", ".join(differing))
    if coverage_uncovered:
        parts.append("unexercised requirements: " + ", ".join(coverage_uncovered))
    return "; ".join(parts)


__all__ = ["review_bundle", "review_loaded"]
