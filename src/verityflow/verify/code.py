"""Linked-router behaviour verifier.

The router code is compared with the effective requirement policy as a routing
function over the same representative ticket space used for the spec and the
workflow. Nothing here trusts rule IDs or destination-name similarity: a finding
is only reported with a witness that was routed through both interpreters.

Runtime ``ERROR`` outcomes (for example ``KeyError`` on an absent field) and
statically rejected constructs are reported separately from behavioural
disagreement, because they have different meanings: an error is a defect on an
evaluated ticket, whereas an unsupported construct leaves part of the artifact
unreviewed.
"""

from __future__ import annotations

from dataclasses import dataclass

from verityflow.ir.model import Expr
from verityflow.ir.policy import Policy, Rule
from verityflow.models.dictionary import DataDictionary
from verityflow.models.report import Finding, FindingCategory, Severity, make_finding
from verityflow.verify.domain import TicketSpace, render_inputs
from verityflow.verify.equivalence import is_reordered, numeric_clue, single_cmp
from verityflow.verify.models import ComparisonRecord, Counterexample
from verityflow.verify.python_ast import CodeProgram, CodeRule
from verityflow.verify.routing import (
    PolicyRouter,
    RouteDifference,
    RouteKind,
    Router,
    RoutingComparison,
    compare_routers,
    describe_outcome,
    make_counterexample,
)

_MAX_COUNTEREXAMPLES_PER_FINDING = 3

_COVERAGE_KINDS = frozenset({RouteKind.UNSUPPORTED, RouteKind.STEP_LIMIT})
_GROUPED_KINDS = frozenset({RouteKind.DESTINATION, RouteKind.NO_MATCH, RouteKind.FALL_THROUGH})


@dataclass(frozen=True, slots=True)
class CodeVerification:
    record: ComparisonRecord
    findings: tuple[Finding, ...]
    routing: RoutingComparison
    counterexamples: tuple[Counterexample, ...]
    workflow_agreement: ComparisonRecord | None = None


def verify_code(
    policy: Policy,
    program: CodeProgram,
    dictionary: DataDictionary,
    space: TicketSpace,
    *,
    policy_ref: str,
    max_signature_cells: int,
    workflow: Router | None = None,
    workflow_ref: str | None = None,
) -> CodeVerification:
    requirements = PolicyRouter(policy, artifact_ref=policy_ref)
    signature = program.routing_signature()
    reordered = _is_reordered(policy, signature, dictionary, max_signature_cells)
    routing = compare_routers(requirements, program, space)

    findings: list[Finding] = []
    counterexamples: list[Counterexample] = []
    covered_refs: set[str | None] = set()

    runtime_groups: dict[str | None, list[RouteDifference]] = {}
    coverage_groups: dict[tuple[RouteKind, str | None], list[RouteDifference]] = {}
    behaviour_groups: dict[tuple[str | None, RouteKind], list[RouteDifference]] = {}
    for difference in routing.differences:
        kind = difference.right.kind
        if kind is RouteKind.ERROR:
            runtime_groups.setdefault(difference.right.detail, []).append(difference)
        elif kind in _COVERAGE_KINDS:
            coverage_groups.setdefault((kind, difference.right.detail), []).append(difference)
        elif kind in _GROUPED_KINDS:
            behaviour_groups.setdefault((difference.left.matched, kind), []).append(difference)

    for detail, differences in runtime_groups.items():
        validated = _validated(
            "code",
            dictionary,
            requirements,
            program,
            differences,
            explanation=(
                "the linked router raised a runtime error on a ticket the requirements "
                "route without error"
            ),
        )
        counterexamples.extend(validated)
        covered_refs.update(difference.left.matched for difference in differences)
        severity = Severity.BLOCKER if validated else Severity.COVERAGE_GAP
        category = FindingCategory.RUNTIME_ERROR if validated else FindingCategory.STRUCTURAL
        findings.append(
            make_finding(
                category,
                severity,
                f"the linked router cannot route {_describe_cell(differences[0])}: {detail}",
                rule_ref=differences[0].left.matched,
                artifact_id=_artifact_id(program),
                artifact_version=_artifact_version(program),
                explanation=(
                    "A runtime error means the reviewer could not establish the output "
                    "for these tickets; it is reported as a defect, not as agreement."
                ),
            )
        )

    for (_kind, detail), differences in coverage_groups.items():
        counterexamples.extend(
            _validated(
                "code",
                dictionary,
                requirements,
                program,
                differences,
                explanation=(
                    "part of the router cannot be interpreted within the supported "
                    "subset; these tickets are unreviewed"
                ),
            )
        )
        covered_refs.update(difference.left.matched for difference in differences)
        qualifier = "sampled" if not space.exhaustive else "evaluated"
        findings.append(
            make_finding(
                FindingCategory.UNSUPPORTED_CODE,
                Severity.COVERAGE_GAP,
                f"the linked router could not be interpreted for "
                f"{len(differences)} {qualifier} ticket combination(s): {detail}",
                rule_ref=differences[0].left.matched,
                artifact_id=_artifact_id(program),
                artifact_version=_artifact_version(program),
                explanation=(
                    "Unsupported constructs and exhausted budgets are disclosed as "
                    "unreviewed regions; they are not treated as agreement and do not "
                    "prove a defect."
                ),
            )
        )

    for (requirement_ref, kind), differences in behaviour_groups.items():
        category, explanation = _diagnose(
            policy,
            signature,
            requirement_ref,
            kind,
            reordered,
        )
        validated = _validated(
            "code",
            dictionary,
            requirements,
            program,
            differences,
            explanation=explanation,
        )
        counterexamples.extend(validated)
        covered_refs.add(requirement_ref)
        severity = Severity.BLOCKER
        if not validated:
            category = FindingCategory.STRUCTURAL
            severity = Severity.COVERAGE_GAP
            explanation = (
                "a behavioural difference was observed but could not be reproduced; "
                "the witness is not reported as a counterexample"
            )
        findings.append(
            make_finding(
                category,
                severity,
                _difference_message(
                    policy,
                    program,
                    differences[0],
                    requirement_ref=requirement_ref,
                    kind=kind,
                ),
                rule_ref=requirement_ref,
                artifact_id=_artifact_id(program),
                artifact_version=_artifact_version(program),
                explanation=explanation,
            )
        )

    agreeing = _agreeing_requirement_refs(requirements, program, space)
    for rule in policy.rules:
        if rule.rule_ref in agreeing or rule.rule_ref in covered_refs:
            continue
        if not space.exhaustive:
            findings.append(
                make_finding(
                    FindingCategory.STRUCTURAL,
                    Severity.COVERAGE_GAP,
                    f"effective requirement {rule.rule_ref!r} (destination "
                    f"{rule.destination!r}) was not exercised by any sampled ticket "
                    "combination, so the linked router is unreviewed there",
                    rule_ref=rule.rule_ref,
                    artifact_id=_artifact_id(program),
                    artifact_version=_artifact_version(program),
                    explanation=(
                        "An unexercised requirement is disclosed as an unreviewed region, "
                        "not as a defect: absence from the sample is not evidence that the "
                        "router lacks the branch."
                    ),
                )
            )
            continue
        findings.append(
            make_finding(
                FindingCategory.SCRIPT_DISAGREEMENT,
                Severity.BLOCKER,
                f"effective requirement {rule.rule_ref!r} (destination "
                f"{rule.destination!r}) has no agreeing branch in the linked router in "
                "any evaluated ticket",
                rule_ref=rule.rule_ref,
                artifact_id=_artifact_id(program),
                artifact_version=_artifact_version(program),
                explanation=(
                    "Requirement identity is attached only after the behaviour was "
                    "observed; rule IDs are never matched against code."
                ),
            )
        )

    record = ComparisonRecord(
        name="code",
        artifact_ref=program.artifact_ref,
        mode=space.mode,
        equal=routing.equal,
        cells_checked=space.checked,
        combinations_total=space.combinations_total,
        combinations_skipped=space.combinations_skipped,
        detail=_routing_detail(routing, program),
        counterexamples=tuple(counterexamples),
        categories=tuple(sorted({finding.category.value for finding in findings})),
    )
    workflow_agreement = (
        _workflow_agreement(
            workflow,
            program,
            dictionary,
            space,
            workflow_ref=workflow_ref,
        )
        if workflow is not None
        else None
    )
    return CodeVerification(
        record=record,
        findings=tuple(findings),
        routing=routing,
        counterexamples=tuple(counterexamples),
        workflow_agreement=workflow_agreement,
    )


def _workflow_agreement(
    workflow: Router,
    program: CodeProgram,
    dictionary: DataDictionary,
    space: TicketSpace,
    *,
    workflow_ref: str | None,
) -> ComparisonRecord:
    routing = compare_routers(workflow, program, space)
    witnesses: list[Counterexample] = []
    if not routing.equal:
        witnesses = _validated(
            "code_vs_workflow",
            dictionary,
            workflow,
            program,
            list(routing.differences),
            explanation=(
                "the linked workflow and the linked router route this ticket to different outcomes"
            ),
        )
    reference = workflow_ref or workflow.artifact_ref
    detail = (
        f"workflow {reference} and the linked router agree on every evaluated ticket"
        if routing.equal
        else f"workflow {reference} and the linked router route {routing.total_differences} "
        "evaluated ticket combination(s) to different outcomes"
    )
    return ComparisonRecord(
        name="code_vs_workflow",
        artifact_ref=program.artifact_ref,
        mode=space.mode,
        equal=routing.equal,
        cells_checked=space.checked,
        combinations_total=space.combinations_total,
        combinations_skipped=space.combinations_skipped,
        detail=detail,
        counterexamples=tuple(witnesses),
        categories=(() if routing.equal else (FindingCategory.SCRIPT_DISAGREEMENT.value,)),
    )


def _validated(
    comparison: str,
    dictionary: DataDictionary,
    left: Router,
    right: Router,
    differences: list[RouteDifference],
    *,
    explanation: str,
) -> list[Counterexample]:
    validated: list[Counterexample] = []
    for difference in differences[:_MAX_COUNTEREXAMPLES_PER_FINDING]:
        candidate = make_counterexample(
            comparison,
            dictionary,
            left,
            right,
            difference,
            explanation=explanation,
        )
        if candidate.validated:
            validated.append(candidate)
    return validated


def _agreeing_requirement_refs(
    requirements: PolicyRouter, program: CodeProgram, space: TicketSpace
) -> frozenset[str]:
    agreeing: set[str] = set()
    for cell in space.cells:
        values = cell.mapping()
        left = requirements.route(values)
        right = program.route(values)
        if (
            left.matched is not None
            and right.kind is left.kind
            and left.destination == right.destination
        ):
            agreeing.add(left.matched)
    return frozenset(agreeing)


def _is_reordered(
    policy: Policy,
    signature: tuple[CodeRule, ...] | None,
    dictionary: DataDictionary,
    max_signature_cells: int,
) -> bool:
    if signature is None:
        return False
    return is_reordered(
        [rule.condition for rule in policy.rules],
        [rule.destination for rule in policy.rules],
        [rule.condition for rule in signature],
        [rule.destination for rule in signature],
        dictionary,
        max_signature_cells,
        firing=True,
    )


def _diagnose(
    policy: Policy,
    signature: tuple[CodeRule, ...] | None,
    requirement_ref: str | None,
    kind: RouteKind,
    reordered: bool,
) -> tuple[FindingCategory, str]:
    requirement = policy.rule(requirement_ref) if requirement_ref is not None else None
    clue = _condition_clue(policy, requirement, signature)
    if reordered:
        return (
            FindingCategory.PRECEDENCE,
            "the router tests equivalent guards in a different order than the "
            "effective requirements; the first-match order changes the outcome",
        )
    if requirement is not None and requirement.exception_of is not None:
        return (
            FindingCategory.MISSING_EXCEPTION,
            f"requirement {requirement_ref!r} is an exception of "
            f"{requirement.exception_of!r} but the router does not implement it",
        )
    if clue is not None:
        return clue
    if kind is RouteKind.NO_MATCH or kind is RouteKind.FALL_THROUGH:
        return (
            FindingCategory.SCRIPT_DISAGREEMENT,
            "the linked router returns no destination for these tickets while the "
            "requirements route them",
        )
    return (
        FindingCategory.SCRIPT_DISAGREEMENT,
        "the linked router and the effective requirements route this ticket to "
        "different destinations",
    )


def _condition_clue(
    policy: Policy, requirement: Rule | None, signature: tuple[CodeRule, ...] | None
) -> tuple[FindingCategory, str] | None:
    if signature is None:
        return None
    if requirement is not None:
        clue = _clue_for_condition(requirement.condition, signature)
        if clue is not None:
            return clue
    # A boundary defect often surfaces on the catch-all requirement (the ticket
    # now falls to a different rule), so look across every requirement boundary
    # on the same field before falling back to a generic disagreement.
    for rule in policy.rules:
        clue = _clue_for_condition(rule.condition, signature)
        if clue is not None:
            return clue
    return None


def _clue_for_condition(
    condition: Expr, signature: tuple[CodeRule, ...]
) -> tuple[FindingCategory, str] | None:
    atom = single_cmp(condition)
    if atom is None:
        return None
    for rule in signature:
        other = single_cmp(rule.condition)
        if other is None or other.field != atom.field:
            continue
        clue = numeric_clue(condition, rule.condition)
        if clue is not None:
            return clue.category, clue.detail
    return None


def _routing_detail(routing: RoutingComparison, program: CodeProgram) -> str:
    if routing.equal:
        return (
            f"the linked router agrees with the effective requirements on every "
            f"evaluated ticket ({program.artifact_ref})"
        )
    suffix = "; additional differences were not retained" if routing.truncated else ""
    return f"{routing.total_differences} ticket combinations route differently{suffix}"


def _describe_cell(difference: RouteDifference) -> str:
    inputs = render_inputs(difference.cell)
    if not inputs:
        return "the empty ticket"
    return ", ".join(
        f"{name}={'<absent>' if value is None else _render(value)}"
        for name, value in inputs.items()
    )


def _difference_message(
    policy: Policy,
    program: CodeProgram,
    difference: RouteDifference,
    *,
    requirement_ref: str | None,
    kind: RouteKind,
) -> str:
    requirement = policy.rule(requirement_ref) if requirement_ref is not None else None
    destination = requirement.destination if requirement is not None else "<default>"
    return (
        f"at {_describe_cell(difference)} the effective requirements route to "
        f"{destination!r} via {requirement_ref or '<default>'}, but the linked router "
        f"({program.path}) yields {describe_outcome(difference.right)} "
        f"({kind.value})"
    )


def _render(value: str | bool | int) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(value)


def _artifact_id(program: CodeProgram) -> str:
    artifact_id, _, _ = program.artifact_ref.partition("@")
    return artifact_id


def _artifact_version(program: CodeProgram) -> str:
    _, _, version = program.artifact_ref.partition("@")
    return version


__all__ = ["CodeProgram", "CodeVerification", "verify_code"]
