from __future__ import annotations

from verityflow.ir.model import And, Const, Expr, NotTrue
from verityflow.ir.normalize import normalize_condition
from verityflow.ir.policy import Policy
from verityflow.mapping.models import ElementKind, Extraction, SemanticElement
from verityflow.models.common import ArtifactRef
from verityflow.models.dictionary import DataDictionary
from verityflow.models.report import Finding, FindingCategory, Severity, make_finding
from verityflow.models.spec import SpecArtifact
from verityflow.models.workflow import NodeKind, WorkflowArtifact

_MAX_WORKFLOW_DEPTH = 64


def extract_requirements(policy: Policy) -> Extraction:
    conditions = [rule.condition for rule in policy.rules]
    return Extraction(
        elements=tuple(
            SemanticElement(
                element_id=rule.rule_ref,
                kind=ElementKind.REQUIREMENT,
                condition=_guarded(conditions, index),
                destination=rule.destination,
                precedence=rule.precedence,
                exception_of=rule.exception_of,
            )
            for index, rule in enumerate(policy.rules)
        )
    )


def extract_spec(
    spec: SpecArtifact,
    dictionary: DataDictionary,
    artifact: ArtifactRef | None,
) -> Extraction:
    findings: list[Finding] = []
    sound = True

    def report(category: FindingCategory, message: str, element: str | None = None) -> None:
        nonlocal sound
        sound = False
        findings.append(
            _finding(
                category,
                severity=Severity.COVERAGE_GAP,
                message=message,
                artifact=artifact,
                element=element,
            )
        )

    priorities = [rule.priority for rule in spec.rules]
    if len(set(priorities)) != len(priorities):
        report(
            FindingCategory.AMBIGUITY,
            "spec declares duplicate rule priorities; the ordered decision list is "
            "undetermined for the tied priorities",
        )
    ordered = sorted(spec.rules, key=lambda rule: rule.priority)
    conditions: list[Expr] = []
    for rule in ordered:
        normalized = normalize_condition(rule.when, dictionary, pointer=f"rules/{rule.id}/when")
        if normalized.expr is None:
            details = "; ".join(problem.message for problem in normalized.problems)
            report(
                FindingCategory.STRUCTURAL,
                f"spec rule {rule.id!r} condition cannot be normalized: {details}",
                rule.id,
            )
            continue
        conditions.append(normalized.expr)
    if not sound:
        return Extraction(elements=(), findings=tuple(findings), sound=False)
    elements: list[SemanticElement] = [
        SemanticElement(
            element_id=rule.id,
            kind=ElementKind.SPEC_RULE,
            condition=_guarded(conditions, index),
            destination=rule.destination,
            precedence=index,
        )
        for index, rule in enumerate(ordered)
    ]
    if spec.default is not None:
        elements.append(
            SemanticElement(
                element_id=f"{spec.spec_id}:default",
                kind=ElementKind.SPEC_DEFAULT,
                condition=_none_true(conditions),
                destination=spec.default.destination,
                precedence=len(ordered),
            )
        )
    return Extraction(elements=tuple(elements), findings=tuple(findings), sound=sound)


def extract_workflow(
    workflow: WorkflowArtifact,
    dictionary: DataDictionary,
    artifact: ArtifactRef | None,
) -> Extraction:
    findings: list[Finding] = []
    sound = True

    def report(
        category: FindingCategory,
        message: str,
        element: str | None = None,
        severity: Severity = Severity.COVERAGE_GAP,
    ) -> None:
        nonlocal sound
        if severity is not Severity.INFO:
            sound = False
        findings.append(
            _finding(
                category, severity=severity, message=message, artifact=artifact, element=element
            )
        )

    nodes = {node.id: node for node in workflow.nodes}
    for node in workflow.nodes:
        if node.version != workflow.version:
            report(
                FindingCategory.STRUCTURAL,
                f"workflow node {node.id!r} has version {node.version!r}, not the "
                f"workflow version {workflow.version!r}",
                node.id,
            )
        if node.kind is NodeKind.DECISION:
            for branch in node.branches:
                if branch.version != workflow.version:
                    report(
                        FindingCategory.STRUCTURAL,
                        f"workflow branch {branch.branch_id!r} has version "
                        f"{branch.version!r}, not the workflow version {workflow.version!r}",
                        branch.branch_id,
                    )
                if branch.target not in nodes:
                    report(
                        FindingCategory.STRUCTURAL,
                        f"workflow branch {branch.branch_id!r} targets unknown node "
                        f"{branch.target!r}",
                        branch.branch_id,
                    )
            if not any(branch.condition is None for branch in node.branches):
                report(
                    FindingCategory.STRUCTURAL,
                    f"workflow decision node {node.id!r} has no else-branch; inputs "
                    "matching no branch have no specified destination",
                    node.id,
                )

    elements: list[SemanticElement] = []

    def visit(
        node_id: str,
        path_condition: Expr,
        trail: tuple[str, ...],
        seen: frozenset[str],
        depth: int,
    ) -> None:
        if depth > _MAX_WORKFLOW_DEPTH:
            report(
                FindingCategory.STRUCTURAL,
                f"workflow traversal exceeded depth {_MAX_WORKFLOW_DEPTH}",
                node_id,
            )
            return
        if node_id in seen:
            report(
                FindingCategory.STRUCTURAL,
                f"workflow contains a cycle through node {node_id!r}",
                node_id,
            )
            return
        node = nodes.get(node_id)
        if node is None:
            return
        if node.kind is NodeKind.TERMINAL:
            destination = node.destination
            if destination is None:
                return
            elements.append(
                SemanticElement(
                    element_id=">".join(trail) if trail else f"{node.id}:terminal",
                    kind=ElementKind.WORKFLOW_PATH,
                    condition=path_condition,
                    destination=destination,
                    precedence=len(elements),
                    detail=f"path to {node.id}",
                )
            )
            return
        prior: list[Expr] = []
        for branch in node.branches:
            if branch.condition is None:
                condition = _none_true(prior)
            else:
                normalized = normalize_condition(
                    branch.condition,
                    dictionary,
                    pointer=f"branches/{branch.branch_id}/condition",
                )
                if normalized.expr is None:
                    details = "; ".join(problem.message for problem in normalized.problems)
                    report(
                        FindingCategory.STRUCTURAL,
                        f"workflow branch {branch.branch_id!r} condition cannot be "
                        f"normalized: {details}",
                        branch.branch_id,
                    )
                    continue
                condition = _fold_conjunction((normalized.expr, *_none_true_factors(prior)))
                prior.append(normalized.expr)
            combined = _fold_conjunction((path_condition, condition))
            visit(
                branch.target,
                combined,
                (*trail, f"{node.id}:{branch.branch_id}"),
                seen | {node.id},
                depth + 1,
            )

    visit(workflow.start, Const(value=True), (), frozenset(), 0)
    return Extraction(elements=tuple(elements), findings=tuple(findings), sound=sound)


def _guarded(conditions: list[Expr], index: int) -> Expr:
    """Condition of entry ``index`` in a first-match list, with earlier blockers.

    Entry ``index`` fires only when its condition is TRUE and no earlier entry
    fired. An earlier entry fires exactly when its raw condition is TRUE (the
    earliest TRUE condition is never blocked), so blocking all earlier raw
    conditions is exact.
    """
    return _fold_conjunction((conditions[index], *_none_true_factors(conditions[:index])))


def _none_true(conditions: list[Expr]) -> Expr:
    return _fold_conjunction(_none_true_factors(conditions))


def _none_true_factors(conditions: list[Expr]) -> tuple[Expr, ...]:
    return tuple(_not_true(condition) for condition in conditions)


def _not_true(expr: Expr) -> Expr:
    if isinstance(expr, Const):
        return Const(value=not expr.value)
    return NotTrue(item=expr)


def _fold_conjunction(items: tuple[Expr, ...]) -> Expr:
    kept: list[Expr] = []
    for item in items:
        if isinstance(item, Const):
            if not item.value:
                return Const(value=False)
            continue
        kept.append(item)
    if not kept:
        return Const(value=True)
    if len(kept) == 1:
        return kept[0]
    return And(items=tuple(kept))


def _finding(
    category: FindingCategory,
    *,
    severity: Severity,
    message: str,
    artifact: ArtifactRef | None,
    element: str | None,
) -> Finding:
    return make_finding(
        category,
        severity,
        message,
        rule_ref=element,
        artifact_id=artifact.artifact_id if artifact is not None else None,
        artifact_version=artifact.version if artifact is not None else None,
    )


__all__ = ["extract_requirements", "extract_spec", "extract_workflow"]
