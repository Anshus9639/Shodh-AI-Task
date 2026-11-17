"""Requirement interaction and exception analysis.

The effective requirement policy is itself executable as an ordered first-match
list, so interactions are analysed on that policy rather than on rule text:

* a rule that never fires in any evaluated ticket is shadowed by earlier rules
  or unsatisfiable;
* an exception that never fires cannot except anything;
* an exception that fires where the rule it excepts does not is misplaced.

These are findings about the requirement set, so their severity is a coverage gap:
the reviewer cannot establish the intended destination even when every artifact
implements the executable policy faithfully.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from verityflow.ir.evaluate import Truth, evaluate
from verityflow.ir.policy import Policy
from verityflow.models.report import Finding, FindingCategory, Severity, make_finding
from verityflow.verify.domain import TicketCell, TicketSpace, render_inputs
from verityflow.verify.routing import PolicyRouter

_REQUIREMENTS = "requirements"


@dataclass(frozen=True, slots=True)
class InteractionAnalysis:
    findings: tuple[Finding, ...]
    unreachable_rules: tuple[str, ...] = ()
    misplaced_exceptions: tuple[str, ...] = ()


def analyze_interactions(policy: Policy, space: TicketSpace) -> InteractionAnalysis:
    router = PolicyRouter(policy, artifact_ref=_REQUIREMENTS)
    fired: Counter[str] = Counter()
    for cell in space.cells:
        outcome = router.route(cell.mapping())
        if outcome.matched is not None:
            fired[outcome.matched] += 1

    qualifier = "evaluated" if space.exhaustive else "sampled"
    findings: list[Finding] = []
    unreachable: list[str] = []
    misplaced: list[str] = []

    for rule in policy.rules:
        if fired[rule.rule_ref]:
            continue
        unreachable.append(rule.rule_ref)
        role = (
            f"the exception for {rule.exception_of!r}"
            if rule.exception_of is not None
            else "the rule"
        )
        findings.append(
            make_finding(
                FindingCategory.INTERACTION,
                Severity.COVERAGE_GAP,
                f"requirement {rule.rule_ref!r} never fires in any {qualifier} ticket "
                f"combination; {role} is shadowed by earlier requirements or cannot be "
                "satisfied",
                rule_ref=rule.rule_ref,
                artifact_id=_REQUIREMENTS,
                explanation=(
                    "A requirement that cannot fire has no observable behaviour, so its "
                    "obligations cannot be verified against any artifact."
                ),
            )
        )

    for rule in policy.rules:
        if rule.exception_of is None or not fired[rule.rule_ref]:
            continue
        parent = policy.rule(rule.exception_of)
        if parent is None:
            continue
        outside: list[str] = []
        for cell in space.cells:
            values = cell.mapping()
            outcome = router.route(values)
            if outcome.matched != rule.rule_ref:
                continue
            if evaluate(parent.condition, values) is Truth.TRUE:
                continue
            outside.append(_render(cell))
            if len(outside) >= 3:
                break
        if not outside:
            continue
        misplaced.append(rule.rule_ref)
        findings.append(
            make_finding(
                FindingCategory.INTERACTION,
                Severity.COVERAGE_GAP,
                f"requirement {rule.rule_ref!r} is declared as an exception of "
                f"{rule.exception_of!r} but fires where that rule does not apply",
                rule_ref=rule.rule_ref,
                artifact_id=_REQUIREMENTS,
                explanation=(
                    "An exception should only narrow the region of the rule it excepts; "
                    "firing outside that region makes the requirement set inconsistent. "
                    f"Witness: {outside[0]}"
                ),
            )
        )

    return InteractionAnalysis(
        findings=tuple(findings),
        unreachable_rules=tuple(unreachable),
        misplaced_exceptions=tuple(misplaced),
    )


def _render(cell: TicketCell) -> str:
    inputs = render_inputs(cell)
    if not inputs:
        return "the empty ticket"
    return ", ".join(
        f"{name}={'<absent>' if value is None else repr(value)}" for name, value in inputs.items()
    )


__all__ = ["InteractionAnalysis", "analyze_interactions"]
