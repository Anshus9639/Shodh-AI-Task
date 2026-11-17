from __future__ import annotations

from dataclasses import dataclass

from verityflow.approvals.resolver import resolve_effective_requirements
from verityflow.config import Config
from verityflow.ingest.artifacts import TypedArtifacts, parse_artifacts
from verityflow.ingest.loader import LoadedBundle, load_bundle
from verityflow.ir.policy import EffectiveRequirements, Policy
from verityflow.mapping.extract import (
    extract_requirements,
    extract_spec,
    extract_workflow,
)
from verityflow.mapping.matcher import map_to_elements
from verityflow.mapping.models import MappingResult, SemanticElement
from verityflow.models.common import ArtifactRef
from verityflow.models.manifest import ArtifactEntry, ArtifactKind
from verityflow.models.report import Finding
from verityflow.requirements.compile import compile_document


@dataclass(frozen=True, slots=True)
class Stage1Result:
    loaded: LoadedBundle
    typed: TypedArtifacts
    entry: ArtifactEntry
    base_policy: Policy
    effective: EffectiveRequirements
    requirements: tuple[SemanticElement, ...]
    mappings: dict[str, MappingResult]
    findings: tuple[Finding, ...]


def run_stage1(bundle_dir, config: Config | None = None) -> Stage1Result:
    active = config or Config()
    loaded = load_bundle(bundle_dir, limits=active.limits)
    typed = parse_artifacts(loaded)
    assert typed.dictionary is not None, "bundle has no data dictionary"
    entry = loaded.manifest.entry(loaded.manifest.review_target.requirements[0])
    assert entry is not None, "review target requirement artifact is not declared"
    parsed = next(item for item in typed.requirements if item.entry.ref == entry.ref)
    base_policy, compile_findings = compile_document(
        parsed.document,
        artifact=ArtifactRef(artifact_id=entry.artifact_id, version=entry.version),
    )
    change_entries = loaded.manifest.entries_of_kind(ArtifactKind.CHANGE_REQUESTS)
    effective = resolve_effective_requirements(
        base_policy,
        manifest=loaded.manifest,
        changes=typed.changes,
        dictionary=typed.dictionary,
        config=active,
        requirements_ref=entry.ref,
        changes_ref=change_entries[0].ref if change_entries else None,
    )
    requirements = extract_requirements(base_policy).elements
    mappings: dict[str, MappingResult] = {}
    if typed.spec is not None:
        spec_entry = loaded.manifest.entries_of_kind(ArtifactKind.SPEC)[0]
        mappings["spec"] = map_to_elements(
            requirements,
            extract_spec(
                typed.spec,
                typed.dictionary,
                ArtifactRef(artifact_id=spec_entry.artifact_id, version=spec_entry.version),
            ),
            typed.dictionary,
            artifact="spec",
            config=active,
            hints=typed.trace.requirement_to_spec if typed.trace is not None else None,
            artifact_ref=ArtifactRef(
                artifact_id=spec_entry.artifact_id, version=spec_entry.version
            ),
            trace_ref=None,
        )
    if typed.workflow is not None:
        workflow_entry = loaded.manifest.entries_of_kind(ArtifactKind.WORKFLOW)[0]
        mappings["workflow"] = map_to_elements(
            requirements,
            extract_workflow(
                typed.workflow,
                typed.dictionary,
                ArtifactRef(artifact_id=workflow_entry.artifact_id, version=workflow_entry.version),
            ),
            typed.dictionary,
            artifact="workflow",
            config=active,
            hints=typed.trace.requirement_to_workflow if typed.trace is not None else None,
            artifact_ref=ArtifactRef(
                artifact_id=workflow_entry.artifact_id, version=workflow_entry.version
            ),
            trace_ref=None,
        )
    findings = (
        *loaded.findings,
        *typed.findings,
        *compile_findings,
        *effective.findings,
        *(item for result in mappings.values() for item in result.findings),
    )
    return Stage1Result(
        loaded=loaded,
        typed=typed,
        entry=entry,
        base_policy=base_policy,
        effective=effective,
        requirements=requirements,
        mappings=mappings,
        findings=findings,
    )
