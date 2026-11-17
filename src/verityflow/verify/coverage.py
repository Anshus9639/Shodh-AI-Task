"""Requirement coverage and evidence mapping.

Each effective requirement rule is exercised over the evaluated ticket space and
mapped to the artifacts that implement it. Coverage is behavioural:

* the rule must fire on at least one evaluated ticket (otherwise its obligations
  are unobservable and the region is disclosed as unreviewed);
* every linked artifact must route *all* of those tickets to the same destination
  (otherwise a disagreement is already reported and the rule is not covered).

Element identifiers (spec rule IDs, workflow node IDs) are recorded as evidence
handles only after the behaviour matched; they are never used as the test itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from verityflow.ir.policy import Policy
from verityflow.models.report import UnreviewedScope
from verityflow.verify.domain import TicketCell, TicketSpace, render_inputs
from verityflow.verify.models import RequirementCoverage
from verityflow.verify.python_ast import CodeProgram
from verityflow.verify.routing import PolicyRouter, RouteKind, Router

_MAX_EVIDENCE = 3


@dataclass(frozen=True, slots=True)
class CoverageAnalysis:
    coverage: tuple[RequirementCoverage, ...]
    unreviewed: tuple[UnreviewedScope, ...]
    uncovered: tuple[str, ...]


def analyze_coverage(
    policy: Policy,
    space: TicketSpace,
    *,
    spec: Router | None = None,
    workflow: Router | None = None,
    code: CodeProgram | None = None,
) -> CoverageAnalysis:
    requirements = PolicyRouter(policy, artifact_ref="requirements")
    qualifier = "evaluated" if space.exhaustive else "sampled"
    coverage: list[RequirementCoverage] = []
    unreviewed: list[UnreviewedScope] = []
    uncovered: list[str] = []

    for rule in policy.rules:
        witnesses: list[TicketCell] = []
        spec_elements: list[str] = []
        workflow_elements: list[str] = []
        evidence: list[str] = []
        spec_ok = True
        workflow_ok = True
        code_ok = True
        code_witness: TicketCell | None = None
        for cell in space.cells:
            values = cell.mapping()
            outcome = requirements.route(values)
            if outcome.matched != rule.rule_ref:
                continue
            witnesses.append(cell)
            if spec is not None:
                spec_outcome = spec.route(values)
                if (
                    spec_outcome.kind is RouteKind.DESTINATION
                    and spec_outcome.destination == rule.destination
                ):
                    _remember(spec_elements, spec_outcome.matched)
                else:
                    spec_ok = False
            if workflow is not None:
                workflow_outcome = workflow.route(values)
                if (
                    workflow_outcome.kind is RouteKind.DESTINATION
                    and workflow_outcome.destination == rule.destination
                ):
                    _remember(workflow_elements, workflow_outcome.matched)
                else:
                    workflow_ok = False
            if code is not None:
                code_outcome = code.route(values)
                if (
                    code_outcome.kind is RouteKind.DESTINATION
                    and code_outcome.destination == rule.destination
                ):
                    if code_witness is None:
                        code_witness = cell
                else:
                    code_ok = False

        if witnesses:
            evidence.append(f"requirement {rule.rule_ref!r} fires at {_render(witnesses[0])}")
        if spec_elements:
            evidence.append(
                f"spec {_artifact(spec)} routes the same tickets via "
                f"{', '.join(sorted(spec_elements))}"
            )
        if workflow_elements:
            evidence.append(
                f"workflow {_artifact(workflow)} routes the same tickets via "
                f"{', '.join(sorted(workflow_elements))}"
            )
        if code is not None and code_witness is not None:
            evidence.append(
                f"router {code.artifact_ref} routes {_render(code_witness)} to {rule.destination!r}"
            )

        covered = bool(witnesses) and spec_ok and workflow_ok and code_ok
        coverage.append(
            RequirementCoverage(
                rule_ref=rule.rule_ref,
                destination=rule.destination,
                spec_elements=tuple(sorted(spec_elements)),
                workflow_elements=tuple(sorted(workflow_elements)),
                code_artifact=code.artifact_ref if code is not None and code_witness else None,
                code_witness=(render_inputs(code_witness) if code_witness is not None else None),
                evidence=tuple(evidence[:_MAX_EVIDENCE]),
                covered=covered,
            )
        )
        if not witnesses:
            uncovered.append(rule.rule_ref)
            unreviewed.append(
                UnreviewedScope(
                    kind="coverage",
                    description=(
                        f"requirement {rule.rule_ref!r} never fired in any {qualifier} "
                        "ticket combination, so its obligations were not exercised"
                    ),
                    rule_refs=(rule.rule_ref,),
                )
            )

    return CoverageAnalysis(
        coverage=tuple(coverage),
        unreviewed=tuple(unreviewed),
        uncovered=tuple(uncovered),
    )


def _remember(elements: list[str], element: str | None) -> None:
    if element is not None and element not in elements:
        elements.append(element)


def _artifact(router: Router | None) -> str:
    return "<none>" if router is None else router.artifact_ref


def _render(cell: TicketCell) -> str:
    inputs = render_inputs(cell)
    if not inputs:
        return "the empty ticket"
    return ", ".join(
        f"{name}={'<absent>' if value is None else repr(value)}" for name, value in inputs.items()
    )


__all__ = ["CoverageAnalysis", "analyze_coverage"]
