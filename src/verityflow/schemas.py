from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from verityflow.ir.policy import EffectiveRequirements, Policy
from verityflow.mapping.models import MappingResult
from verityflow.models.changes import ChangeArtifact
from verityflow.models.dictionary import DataDictionary
from verityflow.models.manifest import BundleManifest
from verityflow.models.report import Finding
from verityflow.models.spec import SpecArtifact
from verityflow.models.trace import TraceMapArtifact
from verityflow.models.workflow import WorkflowArtifact

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
