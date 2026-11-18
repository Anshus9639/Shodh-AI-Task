from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from verityflow.evaluation.models import (
    AblationReport,
    CounterexamplesReport,
    CoverageReport,
    DatasetManifest,
    DebuggingExamplesReport,
    EvaluationMetrics,
    FindingsReport,
    SealedResults,
)
from verityflow.ir.policy import EffectiveRequirements, Policy
from verityflow.mapping.models import MappingResult
from verityflow.model.models import ModelAnalysis, ModelRequest, ModelResponse
from verityflow.models.changes import ChangeArtifact
from verityflow.models.dictionary import DataDictionary
from verityflow.models.evidence import EvidenceReference
from verityflow.models.manifest import ApprovalRecord, ArtifactEntry, BundleManifest, CodeLink
from verityflow.models.report import Finding
from verityflow.models.requirements import AnchoredRequirement, RequirementsSource
from verityflow.models.spec import SpecArtifact, SpecDefault, SpecRule
from verityflow.models.trace import TraceMapArtifact
from verityflow.models.workflow import Branch, WorkflowArtifact, WorkflowNode
from verityflow.verify.models import ConflictAnalysis

SCHEMA_MODELS: dict[str, type[BaseModel]] = {
    "bundle_manifest": BundleManifest,
    "data_dictionary": DataDictionary,
    "spec": SpecArtifact,
    "workflow": WorkflowArtifact,
    "change_requests": ChangeArtifact,
    "trace_map": TraceMapArtifact,
    "policy": Policy,
    "effective_requirements": EffectiveRequirements,
    "mapping_result": MappingResult,
    "finding": Finding,
    "evidence_reference": EvidenceReference,
    "conflict_analysis": ConflictAnalysis,
    "requirements_source": RequirementsSource,
    "anchored_requirement": AnchoredRequirement,
    "spec_rule": SpecRule,
    "spec_default": SpecDefault,
    "workflow_node": WorkflowNode,
    "workflow_branch": Branch,
    "artifact_entry": ArtifactEntry,
    "code_link": CodeLink,
    "approval_record": ApprovalRecord,
    "model_request": ModelRequest,
    "model_response": ModelResponse,
    "model_analysis": ModelAnalysis,
    "evaluation_metrics": EvaluationMetrics,
    "evaluation_findings": FindingsReport,
    "evaluation_coverage": CoverageReport,
    "evaluation_counterexamples": CounterexamplesReport,
    "evaluation_dataset": DatasetManifest,
    "evaluation_sealed_results": SealedResults,
    "evaluation_ablation": AblationReport,
    "evaluation_debugging": DebuggingExamplesReport,
}


def build_schemas() -> dict[str, dict[str, object]]:
    return {
        name: model.model_json_schema(mode="validation")
        for name, model in sorted(SCHEMA_MODELS.items())
    }


def export_schemas(directory: Path) -> tuple[Path, ...]:
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in build_schemas().items():
        path = directory / f"{name}.schema.json"
        path.write_text(
            json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return tuple(written)


__all__ = ["SCHEMA_MODELS", "build_schemas", "export_schemas"]
