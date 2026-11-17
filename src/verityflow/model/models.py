"""Provider-neutral model contracts.

Every model interaction is bounded and typed. The deterministic review report is
the authority; these contracts only describe supplementary, evidence-checked
observations. Nothing here can change a finding, approval or review status.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from verityflow.models.common import FrozenModel


class ModelMode(StrEnum):
    OFF = "off"
    FAKE = "fake"
    GEMINI = "gemini"


class ObservationCategory(StrEnum):
    CANDIDATE_MAPPING = "candidate_mapping"
    AMBIGUOUS_REQUIREMENT = "ambiguous_requirement"
    FINDING_EXPLANATION = "finding_explanation"
    RULE_INTERACTION = "rule_interaction"
    SUGGESTED_TEST = "suggested_test"
    EVIDENCE_FOR_REVIEW = "evidence_for_review"


class EvidenceQuote(FrozenModel):
    anchor: str = Field(min_length=1, max_length=512)
    excerpt: str = Field(min_length=1, max_length=2000)


class Observation(FrozenModel):
    observation_id: str = Field(min_length=2, max_length=16, pattern=r"^M[0-9]{2,}$")
    category: ObservationCategory
    rule_reference: str | None = Field(default=None, max_length=64)
    artifact_id: str | None = Field(default=None, max_length=128)
    artifact_version: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=512)
    evidence: tuple[EvidenceQuote, ...] = ()
    explanation: str = Field(min_length=1, max_length=4000)
    suggested_follow_up: str | None = Field(default=None, max_length=1000)


class Ambiguity(FrozenModel):
    ambiguity_id: str = Field(min_length=2, max_length=16, pattern=r"^A[0-9]{2,}$")
    rule_reference: str | None = Field(default=None, max_length=64)
    explanation: str = Field(min_length=1, max_length=2000)
    requires_human_review: bool = True


class SuggestedTest(FrozenModel):
    test_id: str = Field(min_length=2, max_length=16, pattern=r"^T[0-9]{2,}$")
    description: str = Field(min_length=1, max_length=1000)
    inputs: dict[str, str | bool | int | None] = Field(default_factory=dict)
    expected_focus: str | None = Field(default=None, max_length=500)


class ModelResponse(FrozenModel):
    analysis_summary: str = Field(min_length=1, max_length=4000)
    observations: tuple[Observation, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()
    suggested_tests: tuple[SuggestedTest, ...] = ()


class ModelUsage(FrozenModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    synthetic: bool = False


class ModelCallMetadata(FrozenModel):
    provider: str
    model: str
    provider_request_id: str | None = None
    model_version: str | None = None
    finish_reason: str | None = None
    usage: ModelUsage | None = None
    latency_ms: int = Field(default=0, ge=0)
    provider_status: int | None = None
    synthetic: bool = False


class ModelFailureCategory(StrEnum):
    TIMEOUT = "timeout"
    CONNECTION_FAILURE = "connection_failure"
    AUTHENTICATION_FAILURE = "authentication_failure"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    MALFORMED_JSON = "malformed_json"
    TRUNCATED_JSON = "truncated_json"
    SCHEMA_VALIDATION = "schema_validation"
    INVENTED_REFERENCE = "invented_reference"
    EVIDENCE_VALIDATION = "evidence_validation"
    EXCESSIVE_RESPONSE = "excessive_response"
    REQUEST_TOO_LARGE = "request_too_large"
    RETRY_EXHAUSTED = "retry_exhausted"
    PROMPT_CONSTRUCTION = "prompt_construction"


_RETRYABLE = frozenset(
    {
        ModelFailureCategory.TIMEOUT,
        ModelFailureCategory.CONNECTION_FAILURE,
        ModelFailureCategory.RATE_LIMIT,
        ModelFailureCategory.SERVER_ERROR,
    }
)


class ModelFailure(FrozenModel):
    category: ModelFailureCategory
    message: str
    retryable: bool = False
    attempts: int = Field(default=1, ge=1)
    cause: str | None = None

    @classmethod
    def from_category(
        cls,
        category: ModelFailureCategory,
        message: str,
        *,
        attempts: int = 1,
        cause: str | None = None,
    ) -> ModelFailure:
        return cls(
            category=category,
            message=message,
            retryable=category in _RETRYABLE,
            attempts=attempts,
            cause=cause,
        )


class ModelAnalysisStatus(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class RuleSummary(FrozenModel):
    rule_ref: str
    destination: str
    source_text: str | None = None
    exception_of: str | None = None


class FindingSummary(FrozenModel):
    finding_id: str
    severity: str
    category: str
    message: str
    rule_ref: str | None = None
    artifact_id: str | None = None
    artifact_version: str | None = None
    explanation: str | None = None


class ArtifactSummary(FrozenModel):
    artifact_id: str
    version: str
    kind: str
    sha256: str


class CoverageSummary(FrozenModel):
    rule_ref: str
    destination: str
    covered: bool
    spec_elements: tuple[str, ...] = ()
    workflow_elements: tuple[str, ...] = ()
    code_artifact: str | None = None
    evidence: tuple[str, ...] = ()


class UnreviewedSummary(FrozenModel):
    kind: str
    description: str
    rule_refs: tuple[str, ...] = ()
    artifact: str | None = None


class EvidenceSource(FrozenModel):
    """One exact (anchor, excerpt) pair the model may cite."""

    artifact_id: str
    artifact_version: str
    anchor: str
    excerpt: str


class ModelCacheContext(FrozenModel):
    """Every input whose change must invalidate a cached model analysis."""

    bundle_digest: str
    artifact_versions: tuple[str, ...] = ()
    approval_hashes: tuple[str, ...] = ()
    effective_requirements_digest: str
    report_digest: str


class ModelConfiguration(FrozenModel):
    provider: str
    model: str
    prompt_version: str
    temperature: float
    max_output_tokens: int
    timeout_seconds: float
    max_retries: int


class ModelRequest(FrozenModel):
    bundle_id: str
    bundle_version: str
    deterministic_status: str
    deterministic_detail: str | None = None
    exhaustive: bool = False
    confirmed_candidate_id: str | None = None
    applied_changes: tuple[str, ...] = ()
    rules: tuple[RuleSummary, ...] = ()
    findings: tuple[FindingSummary, ...] = ()
    coverage: tuple[CoverageSummary, ...] = ()
    unreviewed: tuple[UnreviewedSummary, ...] = ()
    evidence: tuple[EvidenceSource, ...] = ()
    available_rule_refs: tuple[str, ...] = ()
    available_artifacts: tuple[ArtifactSummary, ...] = ()
    available_fields: tuple[str, ...] = ()
    prompt_version: str
    model: ModelConfiguration
    cache: ModelCacheContext


class ModelRequestProvenance(FrozenModel):
    provider: str
    model: str
    prompt_version: str
    generation: dict[str, str | float | int]
    request_sha256: str
    input_bytes: int = Field(ge=0)
    timestamp: str
    timeout_seconds: float


class ModelResponseProvenance(FrozenModel):
    provider_request_id: str | None = None
    model_version: str | None = None
    response_sha256: str | None = None
    finish_reason: str | None = None
    usage: ModelUsage | None = None
    latency_ms: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    provider_status: int | None = None
    failure_category: str | None = None
    synthetic: bool = False


class ObservationClassification(StrEnum):
    ASSISTED = "assisted"
    UNVERIFIED = "unverified"


class ClassifiedObservation(FrozenModel):
    observation: Observation
    classification: ObservationClassification
    deterministic_consistency: str
    note: str


class RejectedObservation(FrozenModel):
    observation_id: str | None = None
    category: str | None = None
    reason: ModelFailureCategory
    detail: str


class ModelAnalysis(FrozenModel):
    status: ModelAnalysisStatus
    provider: str
    model: str
    prompt_version: str
    synthetic: bool = False
    deterministic_status: str
    status_unchanged: bool = True
    summary: str | None = None
    confirmed_findings: tuple[FindingSummary, ...] = ()
    assisted_observations: tuple[ClassifiedObservation, ...] = ()
    unverified_observations: tuple[ClassifiedObservation, ...] = ()
    rejected_observations: tuple[RejectedObservation, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()
    suggested_tests: tuple[SuggestedTest, ...] = ()
    request: ModelRequestProvenance
    response: ModelResponseProvenance | None = None
    failure: ModelFailure | None = None
    skipped_reason: str | None = None
    cache_key: str | None = None
    cache_hit: bool = False


__all__ = [
    "Ambiguity",
    "ArtifactSummary",
    "ClassifiedObservation",
    "CoverageSummary",
    "EvidenceQuote",
    "EvidenceSource",
    "FindingSummary",
    "ModelAnalysis",
    "ModelAnalysisStatus",
    "ModelCacheContext",
    "ModelCallMetadata",
    "ModelConfiguration",
    "ModelFailure",
    "ModelFailureCategory",
    "ModelMode",
    "ModelRequest",
    "ModelRequestProvenance",
    "ModelResponse",
    "ModelResponseProvenance",
    "ModelUsage",
    "Observation",
    "ObservationCategory",
    "ObservationClassification",
    "RejectedObservation",
    "RuleSummary",
    "SuggestedTest",
    "UnreviewedSummary",
]
