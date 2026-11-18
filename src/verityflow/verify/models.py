from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from verityflow.ir.policy import UndeterminedScope
from verityflow.models.common import FrozenModel
from verityflow.models.evidence import EvidenceReference
from verityflow.models.report import CheckResult, Finding, UnreviewedScope


class ComparisonMode(StrEnum):
    EXHAUSTIVE = "exhaustive"
    SAMPLED = "sampled"
    UNREVIEWED = "unreviewed"


class Counterexample(FrozenModel):
    """A concrete ticket where two supported interpreters disagree.

    ``validated`` is only true when the disagreement was reproduced by routing
    ``inputs`` through both interpreters again after the witness was recorded.
    """

    comparison: str
    requirement_ref: str | None = None
    artifact_ref: str | None = None
    inputs: dict[str, str | bool | int | None] = Field(default_factory=dict)
    requirement_outcome: str
    artifact_outcome: str
    validated: bool
    explanation: str
    requirement_trace: tuple[str, ...] = ()
    artifact_trace: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceReference, ...] = ()


class ConflictAnalysis(FrozenModel):
    """What a disputed approval leaves determined and what remains undetermined."""

    confirmed_base: str
    affected_change_id: str | None = None
    affected_rule: str | None = None
    conflicting_approval_ids: tuple[str, ...] = ()
    diagnostic_variants: tuple[str, ...] = ()
    affected_conditions: tuple[str, ...] = ()
    independent_findings: tuple[str, ...] = ()
    undetermined_scope: tuple[UndeterminedScope, ...] = ()


class ComparisonRecord(FrozenModel):
    name: str
    artifact_ref: str
    mode: ComparisonMode
    equal: bool
    cells_checked: int
    combinations_total: int
    combinations_skipped: int
    detail: str | None = None
    counterexamples: tuple[Counterexample, ...] = ()
    categories: tuple[str, ...] = ()


class RequirementCoverage(FrozenModel):
    rule_ref: str
    destination: str
    spec_elements: tuple[str, ...] = ()
    workflow_elements: tuple[str, ...] = ()
    code_artifact: str | None = None
    code_witness: dict[str, str | bool | int | None] | None = None
    evidence: tuple[str, ...] = ()
    covered: bool


class ReviewStatus(StrEnum):
    PASS = "PASS"  # noqa: S105 - status label, not a credential
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    INCOMPLETE = "INCOMPLETE"


class ReviewReport(FrozenModel):
    bundle_id: str
    bundle_version: str
    status: ReviewStatus
    requirements_ref: str | None = None
    confirmed_candidate_id: str | None = None
    applied_changes: tuple[str, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    comparisons: tuple[ComparisonRecord, ...] = ()
    coverage: tuple[RequirementCoverage, ...] = ()
    findings: tuple[Finding, ...] = ()
    unreviewed: tuple[UnreviewedScope, ...] = ()
    conflict_analysis: tuple[ConflictAnalysis, ...] = ()
    exhaustive: bool = False
    detail: str | None = None


__all__ = [
    "ComparisonMode",
    "ComparisonRecord",
    "ConflictAnalysis",
    "Counterexample",
    "RequirementCoverage",
    "ReviewReport",
    "ReviewStatus",
]
