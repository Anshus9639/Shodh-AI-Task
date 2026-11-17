"""Common routing outcomes and comparison for every Stage 2 interpreter.

Ordered first-match semantics are expressed directly: walk entries in order and
fire on the first ``TRUE`` evaluation. Kleene ``UNKNOWN`` never fires an entry,
so evaluation falls through to a later entry or the catch-all, matching the
``NotTrue`` guards produced by Stage 1 extraction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from verityflow.ir.evaluate import CellValue, Truth, evaluate
from verityflow.ir.model import Expr
from verityflow.ir.normalize import normalize_condition
from verityflow.ir.policy import Policy
from verityflow.models.common import ArtifactRef
from verityflow.models.conditions import to_ir
from verityflow.models.dictionary import DataDictionary
from verityflow.models.report import Finding, FindingCategory, Severity, make_finding
from verityflow.models.spec import SpecArtifact
from verityflow.verify.domain import TicketCell, TicketSpace, cell_from_inputs, render_inputs
from verityflow.verify.models import Counterexample


class RouteKind(StrEnum):
    DESTINATION = "destination"
    NO_MATCH = "no_match"
    FALL_THROUGH = "fall_through"
    ERROR = "error"
    UNSUPPORTED = "unsupported"
    STEP_LIMIT = "step_limit"


@dataclass(frozen=True, slots=True)
class RouteOutcome:
    kind: RouteKind
    destination: str | None = None
    matched: str | None = None
    trace: tuple[str, ...] = ()
    detail: str | None = None
    nodes: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return describe_outcome(self)


def describe_outcome(outcome: RouteOutcome) -> str:
    if outcome.kind is RouteKind.DESTINATION:
        return f"destination {outcome.destination!r}"
    label = outcome.kind.value
    if outcome.detail:
        return f"{label}: {outcome.detail}"
    return label


class Router(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def artifact_ref(self) -> str: ...

    def route(self, values: Mapping[str, CellValue]) -> RouteOutcome: ...


@dataclass(frozen=True, slots=True)
class RouteEntry:
    ref: str
    condition: Expr
    destination: str


@dataclass(frozen=True, slots=True)
class PolicyRouter:
    policy: Policy
    artifact_ref: str
    name: str = "requirements"

    def route(self, values: Mapping[str, CellValue]) -> RouteOutcome:
        for rule in self.policy.rules:
            if evaluate(rule.condition, values) is Truth.TRUE:
                return RouteOutcome(
                    RouteKind.DESTINATION,
                    destination=rule.destination,
                    matched=rule.rule_ref,
                )
        return RouteOutcome(RouteKind.NO_MATCH, detail="no requirement rule matched")


@dataclass(frozen=True, slots=True)
class SpecProgram:
    router: SpecRouter
    findings: tuple[Finding, ...] = ()
    problem: str | None = None


@dataclass(frozen=True, slots=True)
class SpecRouter:
    entries: tuple[RouteEntry, ...]
    default_destination: str | None
    default_ref: str | None
    artifact_ref: str
    problem: str | None = None
    name: str = "spec"

    def route(self, values: Mapping[str, CellValue]) -> RouteOutcome:
        if self.problem is not None:
            return RouteOutcome(RouteKind.UNSUPPORTED, detail=self.problem)
        for entry in self.entries:
            if evaluate(entry.condition, values) is Truth.TRUE:
                return RouteOutcome(
                    RouteKind.DESTINATION,
                    destination=entry.destination,
                    matched=entry.ref,
                )
        if self.default_destination is not None:
            return RouteOutcome(
                RouteKind.DESTINATION,
                destination=self.default_destination,
                matched=self.default_ref,
            )
        return RouteOutcome(RouteKind.NO_MATCH, detail="no spec rule matched")


def build_spec_router(
    spec: SpecArtifact,
    dictionary: DataDictionary,
    artifact: ArtifactRef,
) -> SpecProgram:
    findings: list[Finding] = []
    artifact_ref = str(artifact)

    def report(category: FindingCategory, message: str, element: str | None = None) -> None:
        findings.append(
            make_finding(
                category,
                Severity.COVERAGE_GAP,
                message,
                rule_ref=element,
                artifact_id=artifact.artifact_id,
                artifact_version=artifact.version,
            )
        )

    priorities = [rule.priority for rule in spec.rules]
    if len(set(priorities)) != len(priorities):
        problem = "spec declares duplicate rule priorities; the first-match order is undetermined"
        report(FindingCategory.AMBIGUITY, problem)
        return SpecProgram(
            router=SpecRouter(
                entries=(),
                default_destination=None,
                default_ref=None,
                artifact_ref=artifact_ref,
                problem=problem,
            ),
            findings=tuple(findings),
            problem=problem,
        )

    entries: list[RouteEntry] = []
    for rule in sorted(spec.rules, key=lambda item: item.priority):
        normalized = normalize_condition(
            to_ir(rule.when), dictionary, pointer=f"rules/{rule.id}/when"
        )
        if normalized.expr is None:
            details = "; ".join(problem.message for problem in normalized.problems)
            problem = f"spec rule {rule.id!r} condition cannot be normalized: {details}"
            report(FindingCategory.STRUCTURAL, problem, rule.id)
            return SpecProgram(
                router=SpecRouter(
                    entries=(),
                    default_destination=None,
                    default_ref=None,
                    artifact_ref=artifact_ref,
                    problem=problem,
                ),
                findings=tuple(findings),
                problem=problem,
            )
        entries.append(
            RouteEntry(ref=rule.id, condition=normalized.expr, destination=rule.destination)
        )

    default_destination = spec.default.destination if spec.default is not None else None
    default_ref = f"{spec.spec_id}:default" if spec.default is not None else None
    return SpecProgram(
        router=SpecRouter(
            entries=tuple(entries),
            default_destination=default_destination,
            default_ref=default_ref,
            artifact_ref=artifact_ref,
        ),
        findings=tuple(findings),
    )


@dataclass(frozen=True, slots=True)
class RouteDifference:
    cell: TicketCell
    left: RouteOutcome
    right: RouteOutcome


@dataclass(frozen=True, slots=True)
class RoutingComparison:
    space: TicketSpace
    differences: tuple[RouteDifference, ...]
    total_differences: int
    truncated: bool

    @property
    def equal(self) -> bool:
        return self.total_differences == 0


def compare_routers(
    left: Router,
    right: Router,
    space: TicketSpace,
    *,
    max_differences: int = 200,
) -> RoutingComparison:
    differences: list[RouteDifference] = []
    total = 0
    for cell in space.cells:
        values = cell.mapping()
        left_outcome = left.route(values)
        right_outcome = right.route(values)
        if describe_outcome(left_outcome) == describe_outcome(right_outcome):
            continue
        total += 1
        if len(differences) < max_differences:
            differences.append(RouteDifference(cell=cell, left=left_outcome, right=right_outcome))
    return RoutingComparison(
        space=space,
        differences=tuple(differences),
        total_differences=total,
        truncated=total > len(differences),
    )


def validate_difference(left: Router, right: Router, difference: RouteDifference) -> bool:
    values = difference.cell.mapping()
    return describe_outcome(left.route(values)) != describe_outcome(right.route(values))


def make_counterexample(
    comparison: str,
    dictionary: DataDictionary,
    left: Router,
    right: Router,
    difference: RouteDifference,
    *,
    explanation: str,
) -> Counterexample:
    """Re-route the rendered witness through both interpreters before recording it."""
    inputs = render_inputs(difference.cell)
    rebuilt = cell_from_inputs(dictionary, inputs)
    left_outcome = left.route(rebuilt.mapping())
    right_outcome = right.route(rebuilt.mapping())
    validated = describe_outcome(left_outcome) != describe_outcome(right_outcome)
    return Counterexample(
        comparison=comparison,
        requirement_ref=difference.left.matched,
        artifact_ref=difference.right.matched or right.artifact_ref,
        inputs=inputs,
        requirement_outcome=describe_outcome(left_outcome),
        artifact_outcome=describe_outcome(right_outcome),
        validated=validated,
        explanation=explanation,
    )


__all__ = [
    "PolicyRouter",
    "RouteDifference",
    "RouteEntry",
    "RouteKind",
    "RouteOutcome",
    "Router",
    "RoutingComparison",
    "SpecProgram",
    "SpecRouter",
    "build_spec_router",
    "compare_routers",
    "describe_outcome",
    "make_counterexample",
    "validate_difference",
]
