"""Workflow graph interpreter over the canonical ticket space.

Branches are evaluated in document order; the first ``TRUE`` branch fires, and an
else-branch fires when no earlier branch was ``TRUE`` (an ``UNKNOWN`` condition
does not fire a branch and therefore falls through, matching the internal
``NotTrue`` guard semantics). Cycles, missing targets and step exhaustion are
reported as distinct routing outcomes rather than silently stopping.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from verityflow.evidence import SourceCatalog
from verityflow.ir.evaluate import CellValue, EvaluationError, Truth, evaluate
from verityflow.ir.model import Const, Expr, canonical_key
from verityflow.ir.normalize import normalize_condition
from verityflow.ir.policy import Policy, Rule
from verityflow.ir.render import render_clause, render_expr
from verityflow.models.common import ArtifactRef
from verityflow.models.conditions import to_ir
from verityflow.models.dictionary import DataDictionary
from verityflow.models.evidence import EvidenceReference, EvidenceRole
from verityflow.models.report import Finding, FindingCategory, Severity, make_finding
from verityflow.models.workflow import NodeKind, WorkflowArtifact
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
    RouteKind,
    RouteOutcome,
    Router,
    RoutingComparison,
    compare_routers,
    describe_outcome,
    make_counterexample,
)
from verityflow.verify.spec import routing_detail

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
class CompiledBranch:
    branch_id: str
    condition: Expr | None
    target: str
    pointer: str = ""


@dataclass(frozen=True, slots=True)
class CompiledNode:
    node_id: str
    kind: NodeKind
    destination: str | None
    branches: tuple[CompiledBranch, ...]
    pointer: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowRouter:
    workflow_id: str
    version: str
    start: str
    nodes: Mapping[str, CompiledNode]
    max_steps: int
    artifact_ref: str
    problem: str | None = None
    locations: Mapping[str, str] = field(default_factory=dict)
    name: str = "workflow"

    def route(self, values: Mapping[str, CellValue]) -> RouteOutcome:
        if self.problem is not None:
            return RouteOutcome(RouteKind.UNSUPPORTED, detail=self.problem)
        node_id = self.start
        trail: list[str] = []
        visited: list[str] = []
        steps = 0
        while True:
            steps += 1
            if steps > self.max_steps:
                return RouteOutcome(
                    RouteKind.STEP_LIMIT,
                    trace=tuple(trail),
                    nodes=tuple(visited),
                    detail=f"workflow exceeded interpreter_max_steps={self.max_steps}",
                )
            node = self.nodes.get(node_id)
            if node is None:
                return RouteOutcome(
                    RouteKind.UNSUPPORTED,
                    trace=tuple(trail),
                    nodes=tuple(visited),
                    detail=f"branch targets unknown node {node_id!r}",
                )
            if node_id in visited:
                return RouteOutcome(
                    RouteKind.ERROR,
                    trace=tuple(trail),
                    nodes=tuple(visited),
                    detail=f"workflow cycle through node {node_id!r}",
                )
            visited.append(node_id)
            if node.kind is NodeKind.TERMINAL:
                return RouteOutcome(
                    RouteKind.DESTINATION,
                    destination=node.destination,
                    matched=node.node_id,
                    trace=tuple(trail),
                    nodes=tuple(visited),
                )
            for branch in node.branches:
                if branch.condition is None:
                    trail.append(f"{node.node_id}/{branch.branch_id}")
                    node_id = branch.target
                    break
                try:
                    truth = evaluate(branch.condition, values)
                except EvaluationError as exc:
                    return RouteOutcome(
                        RouteKind.ERROR,
                        trace=tuple(trail),
                        nodes=tuple(visited),
                        detail=str(exc),
                    )
                if truth is Truth.TRUE:
                    trail.append(f"{node.node_id}/{branch.branch_id}")
                    node_id = branch.target
                    break
            else:
                return RouteOutcome(
                    RouteKind.NO_MATCH,
                    trace=tuple(trail),
                    nodes=tuple(visited),
                    detail=f"no branch of {node.node_id!r} matched",
                )


@dataclass(frozen=True, slots=True)
class WorkflowProgram:
    router: WorkflowRouter
    findings: tuple[Finding, ...] = ()
    problem: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowVerification:
    record: ComparisonRecord
    findings: tuple[Finding, ...]
    routing: RoutingComparison
    counterexamples: tuple[Counterexample, ...]


def build_workflow_router(
    workflow: WorkflowArtifact,
    dictionary: DataDictionary,
    artifact: ArtifactRef,
    *,
    max_steps: int,
) -> WorkflowProgram:
    findings: list[Finding] = []

    def report(message: str, element: str | None = None) -> None:
        findings.append(
            make_finding(
                FindingCategory.STRUCTURAL,
                Severity.COVERAGE_GAP,
                message,
                rule_ref=element,
                artifact_id=artifact.artifact_id,
                artifact_version=artifact.version,
            )
        )

    nodes: dict[str, CompiledNode] = {}
    locations: dict[str, str] = {}
    for node_position, node in enumerate(workflow.nodes):
        node_pointer = f"/nodes/{node_position}"
        locations[node.id] = node_pointer
        if node.version != workflow.version:
            report(
                f"workflow node {node.id!r} has version {node.version!r}, not the "
                f"workflow version {workflow.version!r}",
                node.id,
            )
        branches: list[CompiledBranch] = []
        for branch_position, branch in enumerate(node.branches):
            branch_pointer = f"{node_pointer}/branches/{branch_position}"
            locations[f"{node.id}/{branch.branch_id}"] = branch_pointer
            if branch.version != workflow.version:
                report(
                    f"workflow branch {branch.branch_id!r} has version {branch.version!r}, "
                    f"not the workflow version {workflow.version!r}",
                    branch.branch_id,
                )
            if branch.target not in {item.id for item in workflow.nodes}:
                report(
                    f"workflow branch {branch.branch_id!r} targets unknown node {branch.target!r}",
                    branch.branch_id,
                )
            condition: Expr | None = None
            if branch.condition is not None:
                normalized = normalize_condition(
                    to_ir(branch.condition),
                    dictionary,
                    pointer=f"branches/{branch.branch_id}/condition",
                )
                if normalized.expr is None:
                    details = "; ".join(problem.message for problem in normalized.problems)
                    problem = (
                        f"workflow branch {branch.branch_id!r} condition cannot be "
                        f"normalized: {details}"
                    )
                    report(problem, branch.branch_id)
                    return WorkflowProgram(
                        router=WorkflowRouter(
                            workflow_id=workflow.workflow_id,
                            version=workflow.version,
                            start=workflow.start,
                            nodes={},
                            max_steps=max_steps,
                            artifact_ref=str(artifact),
                            problem=problem,
                        ),
                        findings=tuple(findings),
                        problem=problem,
                    )
                condition = normalized.expr
            branches.append(
                CompiledBranch(
                    branch_id=branch.branch_id,
                    condition=condition,
                    target=branch.target,
                    pointer=branch_pointer,
                )
            )
        if node.kind is NodeKind.DECISION and not any(
            branch.condition is None for branch in node.branches
        ):
            report(
                f"workflow decision node {node.id!r} has no else-branch; tickets "
                "matching no branch have no specified destination",
                node.id,
            )
        nodes[node.id] = CompiledNode(
            node_id=node.id,
            kind=node.kind,
            destination=node.destination,
            branches=tuple(branches),
            pointer=node_pointer,
        )

    return WorkflowProgram(
        router=WorkflowRouter(
            workflow_id=workflow.workflow_id,
            version=workflow.version,
            start=workflow.start,
            nodes=nodes,
            max_steps=max_steps,
            artifact_ref=str(artifact),
            locations=locations,
        ),
        findings=tuple(findings),
    )


def verify_workflow(
    policy: Policy,
    workflow: WorkflowArtifact,
    dictionary: DataDictionary,
    space: TicketSpace,
    *,
    artifact: ArtifactRef,
    policy_ref: str,
    max_steps: int,
    max_signature_cells: int,
    catalog: SourceCatalog | None = None,
    conflict: ConflictContext | None = None,
) -> WorkflowVerification:
    program = build_workflow_router(workflow, dictionary, artifact, max_steps=max_steps)
    findings: list[Finding] = list(program.findings)
    requirements_router = PolicyRouter(policy, artifact_ref=policy_ref)
    router = program.router

    if router.problem is not None:
        record = ComparisonRecord(
            name="workflow",
            artifact_ref=str(artifact),
            mode=ComparisonMode.UNREVIEWED,
            equal=False,
            cells_checked=0,
            combinations_total=space.combinations_total,
            combinations_skipped=space.combinations_skipped,
            detail=router.problem,
            categories=(FindingCategory.STRUCTURAL.value,),
        )
        return WorkflowVerification(
            record=record,
            findings=tuple(findings),
            routing=RoutingComparison(
                space=space, differences=(), total_differences=0, truncated=False
            ),
            counterexamples=(),
        )

    routing = compare_routers(requirements_router, router, space)
    reordered = _is_reordered(policy, router, dictionary, max_signature_cells)
    agreeing = _agreeing_requirement_refs(requirements_router, router, space)
    counterexamples: list[Counterexample] = []

    grouped: dict[tuple[str | None, str | None], list[RouteDifference]] = {}
    for difference in routing.differences:
        key = (difference.left.matched, difference.right.matched)
        grouped.setdefault(key, []).append(difference)

    implementation = str(artifact)
    independent_refs: set[str] = set()

    for (requirement_ref, node_ref), differences in grouped.items():
        category, explanation, diagnosed_ref = _diagnose(
            policy,
            router,
            requirement_ref,
            differences[0],
            reordered,
            dictionary,
            max_signature_cells,
        )
        finding_ref = diagnosed_ref or requirement_ref
        finding_rule = policy.rule(finding_ref) if finding_ref is not None else None
        semantic = _semantic(category, finding_ref, node_ref, finding_rule, router, differences[0])
        refs = _evidence_refs(
            catalog,
            policy,
            policy_ref,
            implementation,
            finding_ref,
            _implementation_pointer(router, differences[0]),
        )
        severity = Severity.BLOCKER
        validated: list[Counterexample] = []
        explained_by: list[str | None] = []
        for difference in differences[:_MAX_COUNTEREXAMPLES_PER_FINDING]:
            candidate = make_counterexample(
                "workflow",
                dictionary,
                requirements_router,
                router,
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
                    node_ref=node_ref,
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
                "",
            )
            findings.append(
                make_finding(
                    category,
                    Severity.BLOCKER,
                    f"effective requirement {rule.rule_ref!r} (destination "
                    f"{rule.destination!r}) is never reached with an agreeing workflow "
                    "destination in any evaluated ticket",
                    rule_ref=rule.rule_ref,
                    artifact_id=artifact.artifact_id,
                    artifact_version=artifact.version,
                    evidence_refs=refs,
                    semantic=(
                        f"missing|{category.value}|{rule.rule_ref}|{canonical_key(rule.condition)}"
                    ),
                    explanation=(
                        "No evaluated ticket reached this requirement's destination through "
                        "the workflow; rule identifiers are not matched against the graph."
                    ),
                    recommendation=_correction(category, rule, artifact),
                )
            )

    unreachable = _unreachable_nodes(router, space)
    if unreachable:
        qualifier = "evaluated" if space.exhaustive else "sampled"
        findings.append(
            make_finding(
                FindingCategory.UNREACHABLE_NODE,
                Severity.COVERAGE_GAP,
                f"workflow nodes {', '.join(sorted(unreachable))} were not reached by "
                f"any {qualifier} ticket combination",
                artifact_id=artifact.artifact_id,
                artifact_version=artifact.version,
                explanation=(
                    "Reachability is only established for combinations that were "
                    "evaluated; sampled spaces cannot prove a node unreachable."
                ),
            )
        )

    record = ComparisonRecord(
        name="workflow",
        artifact_ref=str(artifact),
        mode=space.mode,
        equal=routing.equal,
        cells_checked=space.checked,
        combinations_total=space.combinations_total,
        combinations_skipped=space.combinations_skipped,
        detail=routing_detail(routing),
        counterexamples=tuple(counterexamples),
        categories=tuple(sorted({finding.category.value for finding in findings})),
    )
    return WorkflowVerification(
        record=record,
        findings=tuple(findings),
        routing=routing,
        counterexamples=tuple(counterexamples),
    )


def _agreeing_requirement_refs(
    requirements: PolicyRouter, artifact: Router, space: TicketSpace
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
    router: WorkflowRouter,
    dictionary: DataDictionary,
    max_signature_cells: int,
) -> bool:
    signature = _single_decision_signature(router)
    if signature is None:
        return False
    conditions, destinations = signature
    return is_reordered(
        [rule.condition for rule in policy.rules],
        [rule.destination for rule in policy.rules],
        conditions,
        destinations,
        dictionary,
        max_signature_cells,
    )


def _single_decision_signature(
    router: WorkflowRouter,
) -> tuple[list[Expr], list[str]] | None:
    decisions = [node for node in router.nodes.values() if node.kind is NodeKind.DECISION]
    if len(decisions) != 1:
        return None
    conditions: list[Expr] = []
    destinations: list[str] = []
    for branch in decisions[0].branches:
        target = router.nodes.get(branch.target)
        if target is None or target.kind is not NodeKind.TERMINAL:
            return None
        if target.destination is None:
            return None
        conditions.append(Const(value=True) if branch.condition is None else branch.condition)
        destinations.append(target.destination)
    return conditions, destinations


def _diagnose(
    policy: Policy,
    router: WorkflowRouter,
    requirement_ref: str | None,
    difference: RouteDifference,
    reordered: bool,
    dictionary: DataDictionary,
    max_signature_cells: int,
) -> tuple[FindingCategory, str, str | None]:
    requirement = policy.rule(requirement_ref) if requirement_ref is not None else None
    if difference.right.kind is RouteKind.NO_MATCH:
        return (
            FindingCategory.MISSING_BRANCH,
            "the workflow has no matching branch for this ticket and does not reach a destination",
            requirement_ref,
        )
    if difference.right.kind is not RouteKind.DESTINATION:
        return (
            FindingCategory.STRUCTURAL,
            f"the workflow could not be executed to a destination: "
            f"{describe_outcome(difference.right)}",
            requirement_ref,
        )
    if requirement is not None and requirement.exception_of is not None:
        return (
            FindingCategory.MISSING_EXCEPTION,
            f"requirement {requirement_ref!r} is an exception of "
            f"{requirement.exception_of!r} but the workflow does not implement it",
            requirement_ref,
        )
    if reordered:
        return (
            FindingCategory.PRECEDENCE,
            "the workflow tests the same branches in a different order than the "
            "effective requirements; the first-match order changes the outcome",
            requirement_ref,
        )
    if requirement is not None:
        branch_condition = _condition_for_destination(router, difference.right.destination)
        if branch_condition is not None:
            if (
                same_behaviour(
                    requirement.condition,
                    branch_condition,
                    dictionary,
                    max_signature_cells,
                )
                and requirement.destination != difference.right.destination
            ):
                return (
                    FindingCategory.WRONG_DESTINATION,
                    f"equivalent routing conditions lead to different destinations "
                    f"({requirement.destination!r} vs {difference.right.destination!r})",
                    requirement_ref,
                )
            clue = numeric_clue(requirement.condition, branch_condition)
            if clue is not None:
                return clue.category, clue.detail, requirement_ref
        counterpart = _branch_condition_for_field(router, requirement.condition)
        if counterpart is not None:
            clue = numeric_clue(requirement.condition, counterpart)
            if clue is not None:
                return clue.category, clue.detail, requirement_ref
    for condition in (
        _condition_for_destination(router, difference.right.destination),
        _branch_condition_for_field(router, requirement.condition) if requirement else None,
    ):
        boundary = numeric_requirement(policy, condition, dictionary, max_signature_cells)
        if boundary is not None:
            rule, clue = boundary
            return clue.category, clue.detail, rule.rule_ref
    return (
        FindingCategory.WRONG_DESTINATION,
        f"the workflow routes this ticket to {difference.right.destination!r} while the "
        "effective requirements route it elsewhere",
        requirement_ref,
    )


def _condition_for_destination(router: WorkflowRouter, destination: str | None) -> Expr | None:
    if destination is None:
        return None
    for node in router.nodes.values():
        if node.kind is not NodeKind.DECISION:
            continue
        for branch in node.branches:
            if branch.condition is None:
                continue
            target = router.nodes.get(branch.target)
            if target is not None and target.destination == destination:
                return branch.condition
    return None


def _branch_condition_for_field(router: WorkflowRouter, condition: Expr) -> Expr | None:
    atom = single_cmp(condition)
    if atom is None:
        return None
    for node in router.nodes.values():
        if node.kind is not NodeKind.DECISION:
            continue
        for branch in node.branches:
            if branch.condition is None:
                continue
            other = single_cmp(branch.condition)
            if other is not None and other.field == atom.field:
                return branch.condition
    return None


def _unreachable_nodes(router: WorkflowRouter, space: TicketSpace) -> frozenset[str]:
    reached: set[str] = set()
    for cell in space.cells:
        reached.update(router.route(cell.mapping()).nodes)
    return frozenset(set(router.nodes) - reached)


def _implementation_pointer(router: WorkflowRouter, difference: RouteDifference) -> str:
    outcome = difference.right
    if outcome.kind is RouteKind.DESTINATION:
        for key in (*reversed(outcome.trace), outcome.matched):
            if key is None:
                continue
            pointer = router.locations.get(key)
            if pointer:
                return pointer
    for node_id in reversed(outcome.nodes):
        pointer = router.locations.get(node_id)
        if pointer:
            return pointer
    return ""


def _semantic(
    category: FindingCategory,
    requirement_ref: str | None,
    node_ref: str | None,
    requirement: Rule | None,
    router: WorkflowRouter,
    difference: RouteDifference,
) -> str:
    left = canonical_key(requirement.condition) if requirement is not None else ""
    if requirement is not None and category in {
        FindingCategory.MISSING_REQUIREMENT,
        FindingCategory.MISSING_BRANCH,
        FindingCategory.MISSING_EXCEPTION,
    }:
        # The behavioural witness and the coverage loop describe the same omission;
        # the absent branch must not make their identities differ.
        return f"missing|{category.value}|{requirement_ref or ''}|{left}"
    branch = _condition_for_destination(router, difference.right.destination)
    right = canonical_key(branch) if branch is not None else ""
    return f"{requirement_ref or ''}|{node_ref or ''}|{category.value}|{left}|{right}"


def _evidence_refs(
    catalog: SourceCatalog | None,
    policy: Policy,
    policy_ref: str,
    implementation: str,
    requirement_ref: str | None,
    pointer: str,
) -> tuple[EvidenceReference, ...]:
    if catalog is None:
        return ()
    refs: list[EvidenceReference] = []
    rule = policy.rule(requirement_ref) if requirement_ref is not None else None
    if rule is not None:
        source = catalog.span_reference(policy_ref, rule.anchor, EvidenceRole.SOURCE_REQUIREMENT)
        if source is not None:
            refs.append(source)
    if pointer:
        target = catalog.pointer_reference(implementation, pointer, EvidenceRole.WORKFLOW_BRANCH)
        if target is not None:
            refs.append(target)
    return tuple(refs)


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
            f"Reorder the branches of {label} so they follow the effective requirement "
            f"order and the first matching branch produces the required destination; "
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
            f"Add a branch to {label} for {render_expr(requirement.condition)} -> "
            f"{requirement.destination!r} instead of falling through."
        )
    if category is FindingCategory.MISSING_EXCEPTION:
        return (
            f"Add the exception branch for {render_expr(requirement.condition)} -> "
            f"{requirement.destination!r} in {label}."
        )
    if category is FindingCategory.MISSING_REQUIREMENT:
        return f"Add a workflow branch implementing the effective requirement {clause}."
    return f"Align {label} with the effective requirement {clause}."


def _difference_message(
    policy: Policy,
    *,
    difference: RouteDifference,
    requirement_ref: str | None,
    node_ref: str | None,
) -> str:
    inputs = render_inputs(difference.cell)
    rendered = ", ".join(
        f"{name}={'<absent>' if value is None else _text(value)}" for name, value in inputs.items()
    )
    requirement = policy.rule(requirement_ref) if requirement_ref is not None else None
    destination = requirement.destination if requirement is not None else "<default>"
    return (
        f"at {rendered or 'the empty ticket'} the effective requirements route to "
        f"{destination!r} via {requirement_ref or '<default>'}, but the workflow ends at "
        f"{describe_outcome(difference.right)} via {node_ref or '<no node>'}"
    )


def _text(value: str | bool | int) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(value)


__all__ = [
    "CompiledBranch",
    "CompiledNode",
    "WorkflowProgram",
    "WorkflowRouter",
    "WorkflowVerification",
    "build_workflow_router",
    "verify_workflow",
]
