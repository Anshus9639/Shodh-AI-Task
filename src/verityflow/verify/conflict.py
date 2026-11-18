"""Conflict-aware re-evaluation of behavioural witnesses.

When an approval is disputed, the confirmed policy is the base plus the
non-conflicted approved changes. That policy is *one permitted interpretation*,
not the only one. A difference between an artifact and the confirmed policy is
therefore re-evaluated under every diagnostic candidate: if a candidate
reproduces the artifact outcome, the difference depends on the unresolved
approval and is reported as an undetermined conflict rather than a blocker.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from verityflow.ir.evaluate import CellValue
from verityflow.ir.model import canonical_key
from verityflow.ir.policy import (
    ChangeDecision,
    ChangeState,
    EffectiveRequirements,
    Policy,
    UndeterminedScope,
)
from verityflow.models.report import Finding, Severity
from verityflow.verify.models import ConflictAnalysis
from verityflow.verify.routing import PolicyRouter, describe_outcome


@dataclass(frozen=True, slots=True)
class ConflictContext:
    variants: tuple[tuple[str, Policy], ...]
    artifact_ref: str

    @classmethod
    def from_effective(cls, effective: EffectiveRequirements, artifact_ref: str) -> ConflictContext:
        variants = tuple(
            (candidate.candidate_id, candidate)
            for candidate in effective.candidates
            if candidate.diagnostic_only
        )
        return cls(variants=variants, artifact_ref=artifact_ref)

    @property
    def active(self) -> bool:
        return bool(self.variants)

    def explains(self, values: Mapping[str, CellValue], artifact_outcome: str) -> str | None:
        """Return the diagnostic candidate that reproduces the artifact outcome."""
        for candidate_id, policy in self.variants:
            outcome = PolicyRouter(policy, artifact_ref=self.artifact_ref).route(values)
            if describe_outcome(outcome) == artifact_outcome:
                return candidate_id
        return None


def analyze_conflicts(
    effective: EffectiveRequirements, findings: list[Finding]
) -> tuple[ConflictAnalysis, ...]:
    """Describe each unresolved approval conflict and what it leaves undetermined."""
    variants = tuple(
        candidate.candidate_id for candidate in effective.candidates if candidate.diagnostic_only
    )
    decisions: dict[str, ChangeDecision] = {
        f"{decision.change_id}@{decision.change_version}": decision
        for decision in effective.decisions
    }
    confirmed_base = effective.confirmed_candidate_id or "none"
    entries: dict[tuple[str, str], ConflictAnalysis] = {}

    def add(change_ref: str, rule_ref: str, scopes: tuple[UndeterminedScope, ...]) -> None:
        key = (change_ref, rule_ref)
        if key in entries:
            return
        decision = decisions.get(change_ref)
        affected_rules = _split_rules(rule_ref)
        independent = tuple(
            finding.finding_id
            for finding in findings
            if finding.severity is Severity.BLOCKER
            and finding.rule_ref is not None
            and finding.rule_ref in affected_rules
        )
        entries[key] = ConflictAnalysis(
            confirmed_base=confirmed_base,
            affected_change_id=change_ref or None,
            affected_rule=rule_ref or None,
            conflicting_approval_ids=decision.approval_ids if decision is not None else (),
            diagnostic_variants=variants,
            affected_conditions=_conditions_for(effective, change_ref, rule_ref),
            independent_findings=independent,
            undetermined_scope=scopes,
        )

    handled: set[str] = set()
    for change_ref, decision in sorted(decisions.items()):
        if decision.state is not ChangeState.CONFLICT:
            continue
        handled.add(change_ref)
        scopes = tuple(scope for scope in effective.undetermined if change_ref in scope.change_ids)
        rule_ref = _scope_rules(scopes) or _change_rule(effective, change_ref) or change_ref
        add(change_ref, rule_ref, scopes)

    for scope in effective.undetermined:
        pending = [ref for ref in scope.change_ids if ref not in handled]
        for ref in pending:
            scopes = tuple(item for item in effective.undetermined if ref in item.change_ids)
            rule_ref = _scope_rules(scopes) or _change_rule(effective, ref) or ref
            add(ref, rule_ref, scopes)
    return tuple(entries[key] for key in sorted(entries))


def _change_rule(effective: EffectiveRequirements, ref: str) -> str:
    origin = f"change:{ref}"
    for candidate in effective.candidates:
        for rule in candidate.rules:
            if rule.origin == origin:
                return rule.rule_ref
    return ""


def _scope_rules(scopes: tuple[UndeterminedScope, ...]) -> str:
    names: list[str] = []
    for scope in scopes:
        names.extend(_split_rules(scope.rule_ref))
    return ",".join(sorted(set(names)))


def _split_rules(value: str) -> frozenset[str]:
    return frozenset(item for item in value.split(",") if item)


def _conditions_for(
    effective: EffectiveRequirements, change_ref: str, rule_ref: str
) -> tuple[str, ...]:
    wanted = _split_rules(rule_ref)
    origin = f"change:{change_ref}"
    conditions: set[str] = set()
    for candidate in effective.candidates:
        for rule in candidate.rules:
            if rule.rule_ref in wanted or rule.origin == origin:
                conditions.add(canonical_key(rule.condition))
    return tuple(sorted(conditions))


__all__ = ["ConflictContext", "analyze_conflicts"]
