"""Specification semantic verifier.

The spec is compared to the effective requirement policy as a routing function
over the ticket space, never by rule ID or number matching. Findings are grouped
by the pair of entries that fire and classified from evidence:

* PRECEDENCE - both decision lists contain equivalent entries with identical
  destinations but in a different order;
* WRONG_DESTINATION - the firing conditions are equivalent but destinations
  differ;
* THRESHOLD_CHANGED - the same field is bounded differently (a different value,
  or different strictness at the same value; strictness changes shift the
  effective threshold by one grid step);
* WRONG_OPERATOR - the comparison direction changes (for example ``>=`` to
  ``<``);
* MISSING_EXCEPTION / MISSING_REQUIREMENT - a requirement rule has no
  behaviourally agreeing spec entry anywhere in the space;
* MISSING_BRANCH - the spec falls through where the requirements route.

Every recorded counterexample is re-routed through both interpreters.
"""

from __future__ import annotations

from dataclasses import dataclass

from verityflow.ir.model import Const, Expr
from verityflow.ir.policy import Policy
from verityflow.models.common import ArtifactRef
from verityflow.models.dictionary import DataDictionary
from verityflow.models.report import Finding, FindingCategory, Severity, make_finding
from verityflow.models.spec import SpecArtifact
from verityflow.verify.domain import TicketSpace, render_inputs
from verityflow.verify.equivalence import (
    is_reordered,
    numeric_clue,
    same_behaviour,
    single_cmp,
)
from verityflow.verify.models import ComparisonMode, ComparisonRecord, Counterexample
from verityflow.verify.routing import (
    PolicyRouter,
    RouteDifference,
    RouteEntry,
    Router,
    RoutingComparison,
    SpecRouter,
    build_spec_router,
    compare_routers,
    describe_outcome,
    make_counterexample,
)

_MAX_COUNTEREXAMPLES_PER_FINDING = 3


@dataclass(frozen=True, slots=True)
class SpecVerification:
    record: ComparisonRecord
    findings: tuple[Finding, ...]
    routing: RoutingComparison
    counterexamples: tuple[Counterexample, ...]


def verify_spec(
    policy: Policy,
    spec: SpecArtifact,
    dictionary: DataDictionary,
    space: TicketSpace,
    *,
    artifact: ArtifactRef,
    policy_ref: str,
    max_signature_cells: int,
) -> SpecVerification:
    program = build_spec_router(spec, dictionary, artifact)
    findings: list[Finding] = list(program.findings)
    requirements_router = PolicyRouter(policy, artifact_ref=policy_ref)

    if program.router.problem is not None:
        record = ComparisonRecord(
            name="spec",
            artifact_ref=str(artifact),
            mode=ComparisonMode.UNREVIEWED,
            equal=False,
            cells_checked=0,
            combinations_total=space.combinations_total,
            combinations_skipped=space.combinations_skipped,
            detail=program.router.problem,
            categories=(FindingCategory.STRUCTURAL.value,),
        )
        routing = RoutingComparison(
            space=space,
            differences=(),
            total_differences=0,
            truncated=False,
        )
        return SpecVerification(
            record=record,
            findings=tuple(findings),
            routing=routing,
            counterexamples=(),
        )

    routing = compare_routers(requirements_router, program.router, space)
    reordered = _is_reordered(policy, program.router, dictionary, max_signature_cells)
    agreeing = _agreeing_requirement_refs(requirements_router, program.router, space)
    counterexamples: list[Counterexample] = []

    grouped: dict[tuple[str | None, str | None], list[RouteDifference]] = {}
    for difference in routing.differences:
        key = (difference.left.matched, difference.right.matched)
        grouped.setdefault(key, []).append(difference)

    for (requirement_ref, spec_ref), differences in grouped.items():
        category, explanation = _diagnose(
            policy,
            program.router,
            requirement_ref,
            spec_ref,
            reordered,
            dictionary,
            max_signature_cells,
        )
        validated: list[Counterexample] = []
        for difference in differences[:_MAX_COUNTEREXAMPLES_PER_FINDING]:
            candidate = make_counterexample(
                "spec",
                dictionary,
                requirements_router,
                program.router,
                difference,
                explanation=explanation,
            )
            if candidate.validated:
                validated.append(candidate)
                counterexamples.append(candidate)
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
                    difference=differences[0],
                    requirement_ref=requirement_ref,
                    spec_ref=spec_ref,
                ),
                rule_ref=requirement_ref,
                artifact_id=artifact.artifact_id,
                artifact_version=artifact.version,
                explanation=explanation,
            )
        )

    for rule in policy.rules:
        if rule.rule_ref in agreeing:
            continue
        if any(
            counterexample.requirement_ref == rule.rule_ref for counterexample in counterexamples
        ):
            category = (
                FindingCategory.MISSING_EXCEPTION
                if rule.exception_of is not None
                else FindingCategory.MISSING_REQUIREMENT
            )
            findings.append(
                make_finding(
                    category,
                    Severity.BLOCKER,
                    f"effective requirement {rule.rule_ref!r} (destination "
                    f"{rule.destination!r}) has no semantically agreeing spec entry in "
                    "any evaluated ticket; rule IDs are not used for this comparison",
                    rule_ref=rule.rule_ref,
                    artifact_id=artifact.artifact_id,
                    artifact_version=artifact.version,
                )
            )

    categories = tuple(sorted({finding.category.value for finding in findings}))
    record = ComparisonRecord(
        name="spec",
        artifact_ref=str(artifact),
        mode=space.mode,
        equal=routing.equal,
        cells_checked=space.checked,
        combinations_total=space.combinations_total,
        combinations_skipped=space.combinations_skipped,
        detail=routing_detail(routing),
        counterexamples=tuple(counterexamples),
        categories=categories,
    )
    return SpecVerification(
        record=record,
        findings=tuple(findings),
        routing=routing,
        counterexamples=tuple(counterexamples),
    )


def routing_detail(routing: RoutingComparison) -> str:
    if routing.equal:
        return "routing functions agree on every evaluated ticket"
    suffix = "; additional differences were not retained" if routing.truncated else ""
    return f"{routing.total_differences} ticket combinations route differently{suffix}"


def _agreeing_requirement_refs(
    requirements: Router, artifact: Router, space: TicketSpace
) -> frozenset[str]:
    agreeing: set[str] = set()
    for cell in space.cells:
        values = cell.mapping()
        left = requirements.route(values)
        right = artifact.route(values)
        if (
            left.matched is not None
            and right.kind is left.kind
            and left.destination == right.destination
        ):
            agreeing.add(left.matched)
    return frozenset(agreeing)


def _is_reordered(
    policy: Policy,
    spec: SpecRouter,
    dictionary: DataDictionary,
    max_signature_cells: int,
) -> bool:
    requirement_conditions = [rule.condition for rule in policy.rules]
    requirement_destinations = [rule.destination for rule in policy.rules]
    spec_conditions = [entry.condition for entry in spec.entries]
    spec_destinations = [entry.destination for entry in spec.entries]
    if spec.default_destination is not None:
        spec_conditions.append(Const(value=True))
        spec_destinations.append(spec.default_destination)
    return is_reordered(
        requirement_conditions,
        requirement_destinations,
        spec_conditions,
        spec_destinations,
        dictionary,
        max_signature_cells,
    )


def _diagnose(
    policy: Policy,
    spec: SpecRouter,
    requirement_ref: str | None,
    spec_ref: str | None,
    reordered: bool,
    dictionary: DataDictionary,
    max_signature_cells: int,
) -> tuple[FindingCategory, str]:
    if reordered:
        return (
            FindingCategory.PRECEDENCE,
            "both decision lists contain equivalent entries with the same destinations "
            "in a different order; the first-match order changes the outcome",
        )
    requirement = policy.rule(requirement_ref) if requirement_ref is not None else None
    spec_entry = _entry(spec, spec_ref)
    if requirement is not None and requirement.exception_of is not None:
        return (
            FindingCategory.MISSING_EXCEPTION,
            f"requirement {requirement_ref!r} is an exception of "
            f"{requirement.exception_of!r} but the spec routes the exception region "
            "differently",
        )
    if requirement is not None and (spec_ref is None or spec_ref == spec.default_ref):
        counterpart = _numeric_counterpart(requirement.condition, spec.entries)
        if counterpart is not None:
            clue = numeric_clue(requirement.condition, counterpart.condition)
            if clue is not None:
                return clue.category, clue.detail
        return (
            FindingCategory.MISSING_BRANCH,
            "the specification has no rule for this ticket and falls through to its "
            "default, while the effective requirements route it to a specific "
            "destination",
        )
    if requirement is not None and spec_entry is not None:
        if same_behaviour(
            requirement.condition,
            spec_entry.condition,
            dictionary,
            max_signature_cells,
        ):
            return (
                FindingCategory.WRONG_DESTINATION,
                f"the firing conditions are equivalent but destinations differ "
                f"({requirement.destination!r} vs {spec_entry.destination!r})",
            )
        clue = numeric_clue(requirement.condition, spec_entry.condition)
        if clue is not None:
            return clue.category, clue.detail
    return (
        FindingCategory.STRUCTURAL,
        "the firing entries differ and no more specific diagnosis applies; the concrete "
        "witness is the evidence",
    )


def _numeric_counterpart(condition: Expr, entries: tuple[RouteEntry, ...]) -> RouteEntry | None:
    atom = single_cmp(condition)
    if atom is None:
        return None
    for entry in entries:
        other = single_cmp(entry.condition)
        if other is not None and other.field == atom.field:
            return entry
    return None


def _entry(spec: SpecRouter, ref: str | None) -> RouteEntry | None:
    for entry in spec.entries:
        if entry.ref == ref:
            return entry
    return None


def _difference_message(
    policy: Policy,
    *,
    difference: RouteDifference,
    requirement_ref: str | None,
    spec_ref: str | None,
) -> str:
    inputs = render_inputs(difference.cell)
    rendered = ", ".join(
        f"{name}={'<absent>' if value is None else _number_or_text(value)}"
        for name, value in inputs.items()
    )
    requirement = policy.rule(requirement_ref) if requirement_ref is not None else None
    destination = requirement.destination if requirement is not None else "<default>"
    return (
        f"at {rendered or 'the empty ticket'} the effective requirements route to "
        f"{destination!r} via {requirement_ref or '<default>'}, but the specification "
        f"routes to {describe_outcome(difference.right)} via {spec_ref or '<default>'}"
    )


def _number_or_text(value: str | bool | int) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(value)


__all__ = ["SpecVerification", "routing_detail", "verify_spec"]
