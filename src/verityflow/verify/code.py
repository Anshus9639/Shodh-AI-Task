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

from verityflow.evidence import SourceCatalog
from verityflow.ir.model import Cmp, Expr, canonical_key
from verityflow.ir.policy import Policy, Rule
from verityflow.ir.render import render_clause, render_expr
from verityflow.models.common import TextSpan
from verityflow.models.dictionary import DataDictionary
from verityflow.models.evidence import EvidenceReference, EvidenceRole
from verityflow.models.report import Finding, FindingCategory, Severity, make_finding
from verityflow.verify.conflict import ConflictContext
from verityflow.verify.domain import TicketSpace, render_inputs
from verityflow.verify.equivalence import (
    firing_equivalent,
    is_reordered,
    numeric_clue,
    single_cmp,
)
from verityflow.verify.models import ComparisonRecord, Counterexample
from verityflow.verify.python_ast import CmpFact, CodeProgram, CodeRule
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
    catalog: SourceCatalog | None = None,
    conflict: ConflictContext | None = None,
) -> CodeVerification:
    requirements = PolicyRouter(policy, artifact_ref=policy_ref)
    signature = program.routing_signature()
    facts = program.comparison_facts()
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
        requirement_ref = differences[0].left.matched
        requirement = policy.rule(requirement_ref) if requirement_ref is not None else None
        refs = _code_refs(catalog, policy, policy_ref, program, requirement_ref, signature, facts)
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
            evidence_refs=refs,
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
                rule_ref=requirement_ref,
                artifact_id=_artifact_id(program),
                artifact_version=_artifact_version(program),
                evidence_refs=refs,
                semantic=(
                    f"{requirement_ref or ''}|{category.value}|{detail}"
                    if requirement is None
                    else f"{requirement_ref}|{category.value}|"
                    f"{canonical_key(requirement.condition)}|{detail}"
                ),
                explanation=(
                    "A runtime error means the reviewer could not establish the output "
                    "for these tickets; it is reported as a defect, not as agreement."
                ),
                recommendation=_correction(category, requirement, program),
            )
        )

    for (_kind, detail), differences in coverage_groups.items():
        requirement_ref = differences[0].left.matched
        refs = _code_refs(catalog, policy, policy_ref, program, requirement_ref, signature, facts)
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
                evidence_refs=refs,
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
                rule_ref=requirement_ref,
                artifact_id=_artifact_id(program),
                artifact_version=_artifact_version(program),
                evidence_refs=refs,
                semantic=f"unsupported|{program.artifact_ref}|{detail}",
                explanation=(
                    "Unsupported constructs and exhausted budgets are disclosed as "
                    "unreviewed regions; they are not treated as agreement and do not "
                    "prove a defect."
                ),
                recommendation=(
                    "Rewrite the linked router within the supported subset documented in "
                    "the review guide, or review this region manually."
                ),
            )
        )

    for (requirement_ref, kind), differences in behaviour_groups.items():
        requirement = policy.rule(requirement_ref) if requirement_ref is not None else None
        category, explanation, diagnosed_ref = _diagnose(
            policy,
            signature,
            facts,
            requirement_ref,
            kind,
            reordered,
            dictionary,
            max_signature_cells,
        )
        finding_ref = diagnosed_ref or requirement_ref
        finding_rule = policy.rule(finding_ref) if finding_ref is not None else None
        refs = _code_refs(catalog, policy, policy_ref, program, finding_ref, signature, facts)
        semantic = _semantic(category, finding_ref, finding_rule)
        witnesses: list[Counterexample] = []
        explained_by: list[str | None] = []
        for difference in differences[:_MAX_COUNTEREXAMPLES_PER_FINDING]:
            candidate = make_counterexample(
                "code",
                dictionary,
                requirements,
                program,
                difference,
                explanation=explanation,
                evidence_refs=refs,
            )
            if not candidate.validated:
                continue
            witnesses.append(candidate)
            counterexamples.append(candidate)
            explained_by.append(
                conflict.explains(difference.cell.mapping(), describe_outcome(difference.right))
                if conflict is not None and conflict.active
                else None
            )
        covered_refs.add(requirement_ref)
        covered_refs.add(finding_ref)
        severity = Severity.BLOCKER
        if not witnesses:
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
                rule_ref=finding_ref,
                artifact_id=_artifact_id(program),
                artifact_version=_artifact_version(program),
                evidence_refs=refs,
                semantic=semantic,
                explanation=explanation,
                recommendation=_correction(category, finding_rule, program),
            )
        )

    agreeing = _agreeing_requirement_refs(requirements, program, space)
    for rule in policy.rules:
        if rule.rule_ref in agreeing or rule.rule_ref in covered_refs:
            continue
        refs = _code_refs(catalog, policy, policy_ref, program, rule.rule_ref, signature, facts)
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
                    evidence_refs=refs,
                    semantic=f"unexercised|{rule.rule_ref}|{canonical_key(rule.condition)}",
                    explanation=(
                        "An unexercised requirement is disclosed as an unreviewed region, "
                        "not as a defect: absence from the sample is not evidence that the "
                        "router lacks the branch."
                    ),
                    recommendation=(
                        "Review this requirement against the router with a ticket space "
                        "that exercises it."
                    ),
                )
            )
            continue
        category, explanation, _ = _diagnose(
            policy,
            signature,
            facts,
            rule.rule_ref,
            RouteKind.DESTINATION,
            reordered,
            dictionary,
            max_signature_cells,
        )
        if category is FindingCategory.SCRIPT_DISAGREEMENT:
            explanation = (
                "no evaluated ticket reaches an agreeing destination for this "
                "requirement, and the extracted comparison sites do not explain the "
                "difference; the disagreement is reported without a more specific cause"
            )
        findings.append(
            make_finding(
                category,
                Severity.BLOCKER,
                f"effective requirement {rule.rule_ref!r} (destination "
                f"{rule.destination!r}) has no agreeing branch in the linked router in "
                "any evaluated ticket",
                rule_ref=rule.rule_ref,
                artifact_id=_artifact_id(program),
                artifact_version=_artifact_version(program),
                evidence_refs=refs,
                semantic=_semantic(category, rule.rule_ref, rule),
                explanation=explanation,
                recommendation=_correction(category, rule, program),
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
    evidence_refs: tuple[EvidenceReference, ...] = (),
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
            evidence_refs=evidence_refs,
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
    facts: tuple[CmpFact, ...],
    requirement_ref: str | None,
    kind: RouteKind,
    reordered: bool,
    dictionary: DataDictionary,
    max_signature_cells: int,
) -> tuple[FindingCategory, str, str | None]:
    requirement = policy.rule(requirement_ref) if requirement_ref is not None else None
    if reordered or _requirement_reordered(
        policy, requirement, signature, dictionary, max_signature_cells
    ):
        return (
            FindingCategory.PRECEDENCE,
            "the router tests equivalent guards in a different order than the "
            "effective requirements; the first-match order changes the outcome",
            requirement_ref,
        )
    destination = _destination_clue(requirement, signature, dictionary, max_signature_cells)
    if destination is not None and requirement is not None:
        return (
            FindingCategory.WRONG_DESTINATION,
            f"the router implements the requirement condition but returns "
            f"{destination!r} instead of {requirement.destination!r}",
            requirement_ref,
        )
    clue = _condition_clue(policy, requirement, signature, facts)
    if clue is not None:
        return clue
    if requirement is not None and requirement.exception_of is not None:
        return (
            FindingCategory.MISSING_EXCEPTION,
            f"requirement {requirement_ref!r} is an exception of "
            f"{requirement.exception_of!r} but the router does not implement it",
            requirement_ref,
        )
    if kind is RouteKind.NO_MATCH or kind is RouteKind.FALL_THROUGH:
        return (
            FindingCategory.SCRIPT_DISAGREEMENT,
            "the linked router returns no destination for these tickets while the "
            "requirements route them",
            requirement_ref,
        )
    return (
        FindingCategory.SCRIPT_DISAGREEMENT,
        "the linked router and the effective requirements route this ticket to "
        "different destinations",
        requirement_ref,
    )


def _destination_clue(
    requirement: Rule | None,
    signature: tuple[CodeRule, ...] | None,
    dictionary: DataDictionary,
    max_signature_cells: int,
) -> str | None:
    """Return the router destination for a guard equivalent to the requirement."""
    if requirement is None or signature is None:
        return None
    for rule in signature:
        if rule.destination == requirement.destination:
            continue
        if firing_equivalent(
            requirement.condition, rule.condition, dictionary, max_signature_cells
        ):
            return rule.destination
    return None


def _requirement_reordered(
    policy: Policy,
    requirement: Rule | None,
    signature: tuple[CodeRule, ...] | None,
    dictionary: DataDictionary,
    max_signature_cells: int,
) -> bool:
    """Detect precedence defects even when another guard has a different boundary.

    A single boundary mismatch prevents whole-list matching, but an unaffected
    pair of requirements can still appear in the opposite order in the router.
    """
    if requirement is None or signature is None:
        return False
    target = _matching_rule(requirement, signature, dictionary, max_signature_cells)
    if target is None:
        return False
    target_index = signature.index(target)
    policy_index = next(
        (index for index, rule in enumerate(policy.rules) if rule.rule_ref == requirement.rule_ref),
        None,
    )
    if policy_index is None:
        return False
    for index, rule in enumerate(policy.rules):
        if index == policy_index:
            continue
        match = _matching_rule(rule, signature, dictionary, max_signature_cells)
        if match is None:
            continue
        code_index = signature.index(match)
        if (index < policy_index and code_index > target_index) or (
            index > policy_index and code_index < target_index
        ):
            return True
    return False


def _matching_rule(
    requirement: Rule,
    signature: tuple[CodeRule, ...],
    dictionary: DataDictionary,
    max_signature_cells: int,
) -> CodeRule | None:
    for rule in signature:
        if rule.destination != requirement.destination:
            continue
        if firing_equivalent(
            requirement.condition, rule.condition, dictionary, max_signature_cells
        ):
            return rule
    return None


def _condition_clue(
    policy: Policy,
    requirement: Rule | None,
    signature: tuple[CodeRule, ...] | None,
    facts: tuple[CmpFact, ...],
) -> tuple[FindingCategory, str, str | None] | None:
    if requirement is not None:
        clue = _clue_for_condition(requirement.condition, signature, facts)
        if clue is not None:
            return clue[0], clue[1], requirement.rule_ref
    # A boundary defect often surfaces on the catch-all requirement (the ticket
    # now falls to a different rule), so look across every requirement boundary
    # on the same field before falling back to a generic disagreement.
    for rule in policy.rules:
        clue = _clue_for_condition(rule.condition, signature, facts)
        if clue is not None:
            return clue[0], clue[1], rule.rule_ref
    return None


def _clue_for_condition(
    condition: Expr,
    signature: tuple[CodeRule, ...] | None,
    facts: tuple[CmpFact, ...],
) -> tuple[FindingCategory, str] | None:
    atom = single_cmp(condition)
    if atom is None:
        return None
    if signature is not None:
        for rule in signature:
            other = single_cmp(rule.condition)
            if other is None or other.field != atom.field:
                continue
            clue = numeric_clue(condition, rule.condition)
            if clue is not None:
                return clue.category, clue.detail
    # A complex presence guard can prevent the whole if/return chain from being
    # representable; the individual comparison sites are still exact AST facts.
    for fact in facts:
        comparison = fact.comparison
        if not isinstance(comparison, Cmp) or comparison.field != atom.field:
            continue
        clue = numeric_clue(condition, comparison)
        if clue is not None:
            return clue.category, clue.detail
    return None


def _semantic(
    category: FindingCategory, requirement_ref: str | None, requirement: Rule | None
) -> str:
    left = canonical_key(requirement.condition) if requirement is not None else ""
    return f"{requirement_ref or ''}|{category.value}|{left}"


def _code_refs(
    catalog: SourceCatalog | None,
    policy: Policy,
    policy_ref: str,
    program: CodeProgram,
    requirement_ref: str | None,
    signature: tuple[CodeRule, ...] | None,
    facts: tuple[CmpFact, ...],
) -> tuple[EvidenceReference, ...]:
    if catalog is None:
        return ()
    refs: list[EvidenceReference] = []
    rule = policy.rule(requirement_ref) if requirement_ref is not None else None
    if rule is not None:
        source = catalog.span_reference(policy_ref, rule.anchor, EvidenceRole.SOURCE_REQUIREMENT)
        if source is not None:
            refs.append(source)
    span = _code_span(program, rule, signature, facts)
    if span is not None:
        reference = catalog.named_block_reference(
            program.artifact_ref,
            span.line_start,
            span.line_end,
            EvidenceRole.PYTHON_CODE,
            col_start=span.col_start,
            col_end=span.col_end,
            excerpt=span.excerpt or "",
        )
        if reference is not None:
            refs.append(reference)
    return tuple(refs)


def _code_span(
    program: CodeProgram,
    requirement: Rule | None,
    signature: tuple[CodeRule, ...] | None,
    facts: tuple[CmpFact, ...],
) -> TextSpan | None:
    atom = single_cmp(requirement.condition) if requirement is not None else None
    if atom is not None:
        for fact in facts:
            other = fact.comparison
            if isinstance(other, Cmp) and other.field == atom.field:
                return _fact_span(program, fact)
    if requirement is not None and signature is not None:
        for rule in signature:
            if rule.destination == requirement.destination:
                return TextSpan(
                    path=program.path,
                    line_start=rule.line,
                    line_end=rule.end_line,
                    col_start=rule.col_start,
                    col_end=rule.col_end,
                    excerpt=rule.excerpt,
                )
    if facts:
        return _fact_span(program, facts[0])
    start, end, segment = program.function_segment()
    if not segment:
        return None
    return TextSpan(path=program.path, line_start=start, line_end=end, excerpt=segment)


def _fact_span(program: CodeProgram, fact: CmpFact) -> TextSpan:
    return TextSpan(
        path=program.path,
        line_start=fact.line_start,
        line_end=fact.line_end,
        col_start=fact.col_start,
        col_end=fact.col_end,
        excerpt=fact.excerpt,
    )


def _correction(category: FindingCategory, requirement: Rule | None, program: CodeProgram) -> str:
    label = program.artifact_ref
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
            f"Reorder the guards of {label} so they follow the effective requirement "
            f"order and the first matching guard produces the required destination; "
            f"the affected requirement is {clause}."
        )
    if category in {FindingCategory.THRESHOLD_CHANGED, FindingCategory.WRONG_OPERATOR}:
        return (
            f"Change the bound for {render_expr(requirement.condition)} in {label} so the "
            f"comparison matches the effective requirement {clause}."
        )
    if category is FindingCategory.WRONG_DESTINATION:
        return (
            f"Return the destination {requirement.destination!r} for "
            f"{render_expr(requirement.condition)} in {label}."
        )
    if category is FindingCategory.RUNTIME_ERROR:
        return (
            f"Guard the lookup that raises for tickets where "
            f"{render_expr(requirement.condition)} applies in {label}, so the router can "
            f"return {requirement.destination!r}."
        )
    if category is FindingCategory.MISSING_BRANCH:
        return (
            f"Add a guard to {label} for {render_expr(requirement.condition)} that "
            f"returns {requirement.destination!r} instead of falling through."
        )
    if category is FindingCategory.MISSING_EXCEPTION:
        return (
            f"Add the exception guard for {render_expr(requirement.condition)} -> "
            f"{requirement.destination!r} in {label}."
        )
    if category in {FindingCategory.MISSING_REQUIREMENT, FindingCategory.SCRIPT_DISAGREEMENT}:
        return f"Add a guard to {label} implementing the effective requirement {clause}."
    return f"Align {label} with the effective requirement {clause}."


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
