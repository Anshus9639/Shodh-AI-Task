from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from verityflow.ir.model import Expr
from verityflow.models.common import FrozenModel, TextSpan
from verityflow.models.report import Finding


class UndeterminedScope(FrozenModel):
    rule_ref: str = Field(min_length=1)
    attribute: str = Field(min_length=1)
    reason: str
    change_ids: tuple[str, ...] = ()


class Rule(FrozenModel):
    rule_ref: str = Field(min_length=1)
    anchor: TextSpan
    condition: Expr
    destination: str = Field(min_length=1)
    precedence: int = Field(ge=0)
    exception_of: str | None = None
    origin: str = "base"
    source_text: str | None = None


class Policy(FrozenModel):
    candidate_id: str = Field(min_length=1)
    diagnostic_only: bool = False
    rules: tuple[Rule, ...] = ()
    applied_changes: tuple[str, ...] = ()
    undetermined: tuple[UndeterminedScope, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Policy:
        refs = [rule.rule_ref for rule in self.rules]
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate rule refs in policy")
        precedences = [rule.precedence for rule in self.rules]
        if precedences != list(range(len(self.rules))):
            raise ValueError("rule precedence must be a dense 0-based ordering")
        return self

    def rule(self, ref: str) -> Rule | None:
        for rule in self.rules:
            if rule.rule_ref == ref:
                return rule
        return None

    @property
    def default_rule(self) -> Rule | None:
        if not self.rules:
            return None
        return self.rules[-1] if self.rules[-1].condition.kind == "const" else None


class ChangeState(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    UNAPPROVED = "unapproved"
    CONFLICT = "conflict"
    INVALID_APPROVAL = "invalid_approval"
    STALE = "stale"
    UNSUPPORTED = "unsupported"


class ChangeDecision(FrozenModel):
    change_id: str
    change_version: str
    state: ChangeState
    reason: str
    approval_ids: tuple[str, ...] = ()
    content_hash: str


class EffectiveRequirements(FrozenModel):
    bundle_id: str
    requirements_ref: str
    candidates: tuple[Policy, ...]
    confirmed_candidate_id: str | None
    undetermined: tuple[UndeterminedScope, ...] = ()
    applied: tuple[str, ...] = ()
    decisions: tuple[ChangeDecision, ...] = ()
    findings: tuple[Finding, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> EffectiveRequirements:
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate candidate ids")
        if self.confirmed_candidate_id is not None and self.confirmed_candidate_id not in ids:
            raise ValueError("confirmed candidate id is not present")
        return self

    def confirmed(self) -> Policy | None:
        if self.confirmed_candidate_id is None:
            return None
        for candidate in self.candidates:
            if candidate.candidate_id == self.confirmed_candidate_id:
                return candidate
        return None

    def candidate(self, candidate_id: str) -> Policy | None:
        for candidate in self.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        return None
