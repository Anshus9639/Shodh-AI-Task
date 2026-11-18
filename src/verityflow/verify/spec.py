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

from verityflow.evidence import SourceCatalog
from verityflow.ir.model import Const, Expr, canonical_key
from verityflow.ir.policy import Policy, Rule
from verityflow.ir.render import render_clause, render_expr
from verityflow.models.common import ArtifactRef
from verityflow.models.dictionary import DataDictionary
from verityflow.models.evidence import EvidenceReference, EvidenceRole
from verityflow.models.report import Finding, FindingCategory, Severity, make_finding
from verityflow.models.spec import SpecArtifact
from verityflow.verify.conflict import ConflictContext
from verityflow.verify.domain import TicketSpace, render_inputs
from verityflow.verify.equivalence import (
    is_reordered,
    numeric_clue,
    numeric_requirement,
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
_ROOT_CAUSE_CATEGORIES = frozenset(
    {
        FindingCategory.THRESHOLD_CHANGED,
        FindingCategory.WRONG_OPERATOR,
        FindingCategory.WRONG_DESTINATION,
        FindingCategory.PRECEDENCE,
        FindingCategory.UNIT_MISMATCH,
        FindingCategory.IMPLEMENTS_UNAPPROVED_CHANGE,
    }
)


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
    catalog: SourceCatalog | None = None,
    conflict: ConflictContext | None = None,
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

    spec_index = {rule.id: position for position, rule in enumerate(spec.rules)}
    implementation = str(artifact)
    independent_refs: set[str] = set()

    for (requirement_ref, spec_ref), differences in grouped.items():
        spec_entry = _entry(program.router, spec_ref)
        category, explanation, diagnosed_ref = _diagnose(
            policy,
            program.router,
            requirement_ref,
            spec_ref,
            reordered,
            dictionary,
            max_signature_cells,
        )
        finding_ref = diagnosed_ref or requirement_ref
        finding_rule = policy.rule(finding_ref) if finding_ref is not None else None
        semantic = _semantic(category, finding_ref, spec_ref, finding_rule, spec_entry)
        refs = _evidence_refs(
            catalog,
            policy,
            policy_ref,
            implementation,
            finding_ref,
            spec_ref,
            spec,
            spec_index,
        )
        validated: list[Counterexample] = []
        explained_by: list[str | None] = []
        for difference in differences[:_MAX_COUNTEREXAMPLES_PER_FINDING]:
            candidate = make_counterexample(
                "spec",
                dictionary,
                requirements_router,
                program.router,
                difference,
                explanation=explanation,
                evidence_refs=refs,
            )
            if not candidate.validated:
                continue
            validated.append(candidate)
            counterexamples.append(candidate)
            explained_by.append(
                conflict.explains(difference.cell.mapping(), describe_outcome(difference.right))
                if conflict is not None and conflict.active
                else None
            )
        severity = Severity.BLOCKER
        if not validated:
            category = FindingCategory.STRUCTURAL
            severity = Severity.COVERAGE_GAP
            explanation = (
                "a behavioural difference was observed but could not be reproduced; "
                "the witness is not reported as a counterexample"
            )
        elif (
            conflict is not None
            and conflict.active
            and all(item is not None for item in explained_by)
        ):
            category = FindingCategory.UNDETERMINED_CONFLICT
            severity = Severity.COVERAGE_GAP
            explanation = (
                "every reproduced witness is matched by an unresolved diagnostic approval "
                f"variant ({', '.join(sorted({item for item in explained_by if item}))}); "
                "the difference depends on an approval conflict and is not a confirmed "
                "defect until that approval is resolved"
            )
        elif (
            conflict is not None
            and conflict.active
            and any(item is not None for item in explained_by)
        ):
            explanation = (
                explanation + " At least one reproduced witness does not depend on the unresolved "
                "approvals."
            )
        if severity is Severity.BLOCKER and requirement_ref is not None:
            independent_refs.add(requirement_ref)
        if severity is Severity.BLOCKER and finding_ref is not None:
            independent_refs.add(finding_ref)
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
                rule_ref=finding_ref,
                artifact_id=artifact.artifact_id,
                artifact_version=artifact.version,
                evidence_refs=refs,
                semantic=semantic,
                explanation=explanation,
                recommendation=_correction(category, finding_rule, artifact),
            )
        )

    for rule in policy.rules:
        if rule.rule_ref in agreeing:
            continue
        if any(
            counterexample.requirement_ref == rule.rule_ref for counterexample in counterexamples
        ):
            if rule.rule_ref not in independent_refs:
                continue
            if any(
                finding.rule_ref == rule.rule_ref
                and finding.severity is Severity.BLOCKER
                and finding.category in _ROOT_CAUSE_CATEGORIES
                for finding in findings
            ):
                continue
            category = (
                FindingCategory.MISSING_EXCEPTION
                if rule.exception_of is not None
                else FindingCategory.MISSING_REQUIREMENT
            )
            refs = _evidence_refs(
                catalog,
                policy,
                policy_ref,
                implementation,
                rule.rule_ref,
                None,
                spec,
                spec_index,
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
                    evidence_refs=refs,
                    semantic=(
                        f"missing|{category.value}|{rule.rule_ref}|{canonical_key(rule.condition)}"
                    ),
                    explanation=(
                        "Requirement identity is attached only after the behaviour was "
                        "observed; no spec entry produced the requirement's destination "
                        "on any evaluated ticket."
                    ),
                    recommendation=_correction(category, rule, artifact),
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
) -> tuple[FindingCategory, str, str | None]:
    if reordered:
        return (
            FindingCategory.PRECEDENCE,
            "both decision lists contain equivalent entries with the same destinations "
            "in a different order; the first-match order changes the outcome",
            requirement_ref,
        )
    requirement = policy.rule(requirement_ref) if requirement_ref is not None else None
    spec_entry = _entry(spec, spec_ref)
    if requirement is not None and requirement.exception_of is not None:
        return (
            FindingCategory.MISSING_EXCEPTION,
            f"requirement {requirement_ref!r} is an exception of "
            f"{requirement.exception_of!r} but the spec routes the exception region "
            "differently",
            requirement_ref,
        )
    if spec_entry is not None:
        boundary = numeric_requirement(
            policy, spec_entry.condition, dictionary, max_signature_cells
        )
        if boundary is not None:
            rule, found = boundary
            return found.category, found.detail, rule.rule_ref
    if requirement is not None and (spec_ref is None or spec_ref == spec.default_ref):
        counterpart = _numeric_counterpart(requirement.condition, spec.entries)
        if counterpart is not None:
            clue = numeric_clue(requirement.condition, counterpart.condition)
            if clue is not None:
                return clue.category, clue.detail, requirement_ref
        return (
            FindingCategory.MISSING_BRANCH,
            "the specification has no rule for this ticket and falls through to its "
            "default, while the effective requirements route it to a specific "
            "destination",
            requirement_ref,
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
                requirement_ref,
            )
        clue = numeric_clue(requirement.condition, spec_entry.condition)
        if clue is not None:
            return clue.category, clue.detail, requirement_ref
    return (
        FindingCategory.STRUCTURAL,
        "the firing entries differ and no more specific diagnosis applies; the concrete "
        "witness is the evidence",
        requirement_ref,
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


def _semantic(
    category: FindingCategory,
    requirement_ref: str | None,
    spec_ref: str | None,
    requirement: Rule | None,
    spec_entry: RouteEntry | None,
) -> str:
    left = canonical_key(requirement.condition) if requirement is not None else ""
    if requirement is not None and category in {
        FindingCategory.MISSING_REQUIREMENT,
        FindingCategory.MISSING_BRANCH,
        FindingCategory.MISSING_EXCEPTION,
    }:
        # The behavioural witness and the coverage loop describe the same omission;
        # the absent entry must not make their identities differ.
        return f"missing|{category.value}|{requirement_ref or ''}|{left}"
    right = canonical_key(spec_entry.condition) if spec_entry is not None else ""
    return f"{requirement_ref or ''}|{spec_ref or 'default'}|{category.value}|{left}|{right}"


def _evidence_refs(
    catalog: SourceCatalog | None,
    policy: Policy,
    policy_ref: str,
    implementation: str,
    requirement_ref: str | None,
    spec_ref: str | None,
    spec: SpecArtifact,
    spec_index: dict[str, int],
) -> tuple[EvidenceReference, ...]:
    if catalog is None:
        return ()
    refs: list[EvidenceReference] = []
    rule = policy.rule(requirement_ref) if requirement_ref is not None else None
    if rule is not None:
        source = catalog.span_reference(policy_ref, rule.anchor, EvidenceRole.SOURCE_REQUIREMENT)
        if source is not None:
            refs.append(source)
    pointer = _spec_pointer(spec, spec_ref, spec_index)
    if pointer is not None:
        implementation_ref = catalog.pointer_reference(
            implementation, pointer, EvidenceRole.SPECIFICATION
        )
        if implementation_ref is not None:
            refs.append(implementation_ref)
    return tuple(refs)


def _spec_pointer(spec: SpecArtifact, spec_ref: str | None, index: dict[str, int]) -> str | None:
    if spec_ref is None:
        return "/default" if spec.default is not None else ""
    if spec_ref == f"{spec.spec_id}:default":
        return "/default" if spec.default is not None else ""
    position = index.get(spec_ref)
    if position is None:
        return "" if spec.default is None else "/default"
    return f"/rules/{position}/when"


def _correction(category: FindingCategory, requirement: Rule | None, artifact: ArtifactRef) -> str:
    label = str(artifact)
    if category is FindingCategory.UNDETERMINED_CONFLICT:
        return (
            "Resolve the conflicting approval records for the affected change; until "
            "the permitted policy is determined this difference is not a confirmed defect."
        )
    if requirement is None:
        return f"Align {label} with the effective requirement policy on the evaluated tickets."
    clause = render_clause(requirement.condition, requirement.destination)
    if category is FindingCategory.PRECEDENCE:
        return (
            f"Reorder the entries of {label} so they follow the effective requirement "
            f"order and the first matching entry produces the required destination; "
            f"the affected requirement is {clause}."
        )
    if category in {FindingCategory.THRESHOLD_CHANGED, FindingCategory.WRONG_OPERATOR}:
        return (
            f"Change the bound for {render_expr(requirement.condition)} in {label} so the "
            f"comparison matches the effective requirement {clause}."
        )
    if category is FindingCategory.WRONG_DESTINATION:
        return (
            f"Route {render_expr(requirement.condition)} to {requirement.destination!r} in {label}."
        )
    if category is FindingCategory.MISSING_BRANCH:
        return (
            f"Add an entry to {label} for {render_expr(requirement.condition)} -> "
            f"{requirement.destination!r} instead of falling through to the default."
        )
    if category is FindingCategory.MISSING_EXCEPTION:
        return (
            f"Add the exception entry for {render_expr(requirement.condition)} -> "
            f"{requirement.destination!r} in {label}."
        )
    if category is FindingCategory.MISSING_REQUIREMENT:
        return f"Add a specification entry implementing the effective requirement {clause}."
    return f"Align {label} with the effective requirement {clause}."


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
