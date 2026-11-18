from __future__ import annotations

import hashlib
from enum import StrEnum

from verityflow.models.common import FrozenModel, Location
from verityflow.models.evidence import EvidenceReference


class Severity(StrEnum):
    BLOCKER = "BLOCKER"
    COVERAGE_GAP = "COVERAGE_GAP"
    WARNING = "WARNING"
    INFO = "INFO"


class FindingCategory(StrEnum):
    MISSING_REQUIREMENT = "MISSING_REQUIREMENT"
    UNTRACEABLE_REQUIREMENT = "UNTRACEABLE_REQUIREMENT"
    UNTRACEABLE_ELEMENT = "UNTRACEABLE_ELEMENT"
    THRESHOLD_CHANGED = "THRESHOLD_CHANGED"
    WRONG_OPERATOR = "WRONG_OPERATOR"
    PRECEDENCE = "PRECEDENCE"
    MISSING_BRANCH = "MISSING_BRANCH"
    WRONG_DESTINATION = "WRONG_DESTINATION"
    UNREACHABLE_NODE = "UNREACHABLE_NODE"
    SCRIPT_DISAGREEMENT = "SCRIPT_DISAGREEMENT"
    MISSING_EXCEPTION = "MISSING_EXCEPTION"
    INTERACTION = "INTERACTION"
    IMPLEMENTS_UNAPPROVED_CHANGE = "IMPLEMENTS_UNAPPROVED_CHANGE"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    UNDETERMINED_CONFLICT = "UNDETERMINED_CONFLICT"
    INVALID_APPROVAL = "INVALID_APPROVAL"
    STALE_APPROVAL = "STALE_APPROVAL"
    STALE_CHANGE = "STALE_CHANGE"
    UNAPPROVED_CHANGE = "UNAPPROVED_CHANGE"
    REJECTED_CHANGE = "REJECTED_CHANGE"
    APPROVAL_IGNORED = "APPROVAL_IGNORED"
    UNSUPPORTED_CHANGE_OPERATION = "UNSUPPORTED_CHANGE_OPERATION"
    UNTRUSTED_APPROVAL_CLAIM = "UNTRUSTED_APPROVAL_CLAIM"
    SUSPECTED_INJECTION = "SUSPECTED_INJECTION"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    UNSUPPORTED_CODE = "UNSUPPORTED_CODE"
    AMBIGUITY = "AMBIGUITY"
    INVALID_TRACE_HINT = "INVALID_TRACE_HINT"
    AMBIGUOUS_MAPPING = "AMBIGUOUS_MAPPING"
    DEFERRED_DESCRIPTOR = "DEFERRED_DESCRIPTOR"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    PROVENANCE_FAILURE = "PROVENANCE_FAILURE"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    STRUCTURAL = "STRUCTURAL"


class EvidenceItem(FrozenModel):
    location: Location
    excerpt: str


class Finding(FrozenModel):
    finding_id: str
    severity: Severity
    category: FindingCategory
    source: str = "deterministic"
    message: str
    rule_ref: str | None = None
    artifact_id: str | None = None
    artifact_version: str | None = None
    location: Location | None = None
    evidence: tuple[EvidenceItem, ...] = ()
    evidence_refs: tuple[EvidenceReference, ...] = ()
    explanation: str | None = None
    recommendation: str | None = None


def make_finding(
    category: FindingCategory,
    severity: Severity,
    message: str,
    *,
    rule_ref: str | None = None,
    artifact_id: str | None = None,
    artifact_version: str | None = None,
    location: Location | None = None,
    evidence: tuple[EvidenceItem, ...] = (),
    evidence_refs: tuple[EvidenceReference, ...] = (),
    semantic: str | None = None,
    explanation: str | None = None,
    recommendation: str | None = None,
) -> Finding:
    """Build a finding with a stable, witness-independent identity.

    ``semantic`` is the defect descriptor (for example the mismatching condition
    pair). When it is supplied the concrete witness cell is not part of the ID,
    so the same defect discovered through several checks merges instead of
    producing one finding per witness.
    """
    identity = semantic if semantic is not None else ""
    seed = "|".join(
        [
            category.value,
            rule_ref or "",
            artifact_id or "",
            artifact_version or "",
            identity,
            "" if semantic is not None else message,
            "" if semantic is not None or location is None else location.model_dump_json(),
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    if location is None and evidence_refs:
        location = evidence_refs[0].to_location()
    if not evidence and evidence_refs:
        evidence = tuple(
            EvidenceItem(location=item.to_location(), excerpt=item.excerpt)
            for item in evidence_refs
        )
    return Finding(
        finding_id=f"F-{category.value}-{digest}",
        severity=severity,
        category=category,
        message=message,
        rule_ref=rule_ref,
        artifact_id=artifact_id,
        artifact_version=artifact_version,
        location=location,
        evidence=evidence,
        evidence_refs=evidence_refs,
        explanation=explanation,
        recommendation=recommendation,
    )


class CheckOutcome(StrEnum):
    PASS = "pass"  # noqa: S105 - status label, not a credential
    FAILED = "failed"
    FAILED_WITH_BLOCKER = "failed_with_blocker"
    COVERAGE_GAP = "coverage_gap"
    SKIPPED = "skipped"


class UnreviewedScope(FrozenModel):
    kind: str
    description: str
    rule_refs: tuple[str, ...] = ()
    artifact: str | None = None


class CheckResult(FrozenModel):
    name: str
    required: bool = True
    outcome: CheckOutcome
    detail: str | None = None
