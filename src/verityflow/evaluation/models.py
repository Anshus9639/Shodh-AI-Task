"""Schema-validated contracts for the Stage 4 evaluation reports."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from verityflow.models.common import FrozenModel


class Ratio(FrozenModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)


class ReportHeader(FrozenModel):
    report: str
    generated_at: str
    code_revision: str
    baseline_revision: str | None = None
    config_sha256: str
    dataset_manifest_sha256: str
    bundle_count: int = Field(ge=0)
    model_execution_mode: str
    unsupported_scope: tuple[str, ...] = ()


class DecisionMetrics(FrozenModel):
    total_cases: int = Field(ge=0)
    expected_counts: dict[str, int]
    actual_counts: dict[str, int]
    correct_pass: Ratio
    false_pass: Ratio
    false_pass_on_defective: Ratio
    correct_changes_required: Ratio
    false_block: Ratio
    correct_incomplete: Ratio
    abstentions_by_expected: dict[str, int]
    overall_correct: Ratio


class FindingMetrics(FrozenModel):
    findings_total: int = Field(ge=0)
    defective_cases: int = Field(ge=0)
    correct_defect_identification: Ratio
    correct_rule_reference: Ratio
    correct_artifact: Ratio
    correct_artifact_version: Ratio
    evidence_location: Ratio
    structured_evidence: Ratio
    valid_evidence: Ratio
    message_witness: Ratio
    valid_counterexamples: Ratio
    blocker_severity_on_defects: Ratio
    unsupported_findings: int = Field(ge=0)
    duplicate_findings: int = Field(ge=0)


class CoverageMetrics(FrozenModel):
    total_effective_requirements: int = Field(ge=0)
    fully_reviewed: int = Field(ge=0)
    partially_reviewed: int = Field(ge=0)
    unreviewed: int = Field(ge=0)
    unsupported_expressions: int = Field(ge=0)
    missing_evidence: int = Field(ge=0)
    source_coverage: Ratio
    specification_coverage: Ratio
    workflow_coverage: Ratio
    code_coverage: Ratio
    evidence_completeness: Ratio
    comparison_modes: dict[str, int]


class CaseRecord(FrozenModel):
    bundle_id: str
    expected_status: str
    actual_status: str
    correct: bool
    runtime_seconds: float = Field(ge=0.0)
    input_bytes: int = Field(ge=0)
    cells_checked: int = Field(ge=0)
    findings: int = Field(ge=0)
    comparison_modes: dict[str, int]
    error: str | None = None


class ResourceMetrics(FrozenModel):
    total_runtime_seconds: float = Field(ge=0.0)
    mean_runtime_seconds: float = Field(ge=0.0)
    bundle_count: int = Field(ge=0)
    input_bytes: int = Field(ge=0)
    counterexample_combinations: int = Field(ge=0)
    model_requests: int = Field(ge=0)
    model_successes: int = Field(ge=0)
    model_failures: int = Field(ge=0)
    model_retries: int = Field(ge=0)
    fake_client_calls: int = Field(ge=0)
    real_model_calls: int = Field(ge=0)


class EvaluationMetrics(FrozenModel):
    header: ReportHeader
    decision: DecisionMetrics
    finding_quality: FindingMetrics
    coverage: CoverageMetrics
    resources: ResourceMetrics
    cases: tuple[CaseRecord, ...]


class FindingsReport(FrozenModel):
    header: ReportHeader
    cases: tuple[dict[str, Any], ...]


class CoverageReport(FrozenModel):
    header: ReportHeader
    cases: tuple[dict[str, Any], ...]


class CounterexamplesReport(FrozenModel):
    header: ReportHeader
    cases: tuple[dict[str, Any], ...]


class DatasetManifest(FrozenModel):
    dataset_id: str
    frozen_on: str
    bundle_count: int = Field(ge=0)
    development_bundle_count: int = Field(ge=0)
    sealed_bundle_count: int = Field(ge=0)
    expected_outcome_counts: dict[str, int]
    implementation_checkpoint: str
    dataset_freeze_commit: str
    config: dict[str, str]
    label_isolation: str
    sealed_manifests: dict[str, str]
    evaluation_manifest_sha256: str
    cases: tuple[dict[str, Any], ...]


class SealedResults(FrozenModel):
    header: ReportHeader
    expected_outcome_counts: dict[str, int]
    actual_outcome_counts: dict[str, int]
    cases: tuple[dict[str, Any], ...]


class AblationReport(FrozenModel):
    header: ReportHeader
    enabled: EvaluationMetrics
    disabled: EvaluationMetrics
    changes: tuple[dict[str, Any], ...]


class DebuggingExamplesReport(FrozenModel):
    header: ReportHeader
    examples: tuple[dict[str, Any], ...]


__all__ = [
    "AblationReport",
    "CaseRecord",
    "CounterexamplesReport",
    "CoverageMetrics",
    "CoverageReport",
    "DatasetManifest",
    "DebuggingExamplesReport",
    "DecisionMetrics",
    "EvaluationMetrics",
    "FindingMetrics",
    "FindingsReport",
    "Ratio",
    "ReportHeader",
    "ResourceMetrics",
    "SealedResults",
]
