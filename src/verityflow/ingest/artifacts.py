from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from verityflow.ingest.loader import LoadedBundle
from verityflow.models.changes import ChangeArtifact, ChangeRequest
from verityflow.models.dictionary import DataDictionary
from verityflow.models.manifest import ArtifactEntry, ArtifactKind
from verityflow.models.report import Finding, FindingCategory, Severity, make_finding
from verityflow.models.spec import SpecArtifact
from verityflow.models.trace import TraceMapArtifact
from verityflow.models.workflow import WorkflowArtifact
from verityflow.requirements.document import RequirementDocument
from verityflow.requirements.parser import parse_requirements

_LABELS = {
    ArtifactKind.DATA_DICTIONARY: "data dictionary",
    ArtifactKind.CHANGE_REQUESTS: "change requests",
    ArtifactKind.TRACE_MAP: "trace map",
    ArtifactKind.SPEC: "specification",
    ArtifactKind.WORKFLOW: "workflow",
}


@dataclass(frozen=True, slots=True)
class ParsedRequirement:
    entry: ArtifactEntry
    document: RequirementDocument


@dataclass(frozen=True, slots=True)
class TypedArtifacts:
    dictionary: DataDictionary | None = None
    changes: ChangeArtifact = field(default_factory=ChangeArtifact)
    trace: TraceMapArtifact | None = None
    spec: SpecArtifact | None = None
    workflow: WorkflowArtifact | None = None
    requirements: tuple[ParsedRequirement, ...] = ()
    findings: tuple[Finding, ...] = ()


def parse_artifacts(loaded: LoadedBundle) -> TypedArtifacts:
    findings: list[Finding] = []
    dictionary = _parse_single(loaded, ArtifactKind.DATA_DICTIONARY, DataDictionary, findings)
    spec = _parse_single(loaded, ArtifactKind.SPEC, SpecArtifact, findings)
    workflow = _parse_single(loaded, ArtifactKind.WORKFLOW, WorkflowArtifact, findings)
    trace = _parse_single(loaded, ArtifactKind.TRACE_MAP, TraceMapArtifact, findings)
    changes = _merge_changes(loaded, findings)

    requirements: list[ParsedRequirement] = []
    requirement_entries = loaded.manifest.entries_of_kind(ArtifactKind.REQUIREMENTS)
    if len(requirement_entries) > 1:
        findings.append(
            make_finding(
                FindingCategory.STRUCTURAL,
                Severity.COVERAGE_GAP,
                f"bundle declares {len(requirement_entries)} requirement artifacts; "
                f"only {requirement_entries[0].ref} can be resolved into the effective "
                "policy and the others are unreviewed",
                artifact_id=requirement_entries[0].artifact_id,
                artifact_version=requirement_entries[0].version,
            )
        )
    if requirement_entries and dictionary is None:
        findings.append(
            make_finding(
                FindingCategory.STRUCTURAL,
                Severity.COVERAGE_GAP,
                "no valid data dictionary was loaded; requirement documents cannot be "
                "parsed, so requirement coverage is unreviewed",
                artifact_id="bundle.json",
                artifact_version=loaded.manifest.bundle_version,
            )
        )
    for entry in requirement_entries:
        content = loaded.text_of(entry.ref)
        if content is None or dictionary is None:
            continue
        document = parse_requirements(content, entry.path, dictionary)
        requirements.append(ParsedRequirement(entry=entry, document=document))

    return TypedArtifacts(
        dictionary=dictionary,
        changes=changes,
        trace=trace,
        spec=spec,
        workflow=workflow,
        requirements=tuple(requirements),
        findings=tuple(findings),
    )


def _parse_single[Model: BaseModel](
    loaded: LoadedBundle,
    kind: ArtifactKind,
    model: type[Model],
    findings: list[Finding],
) -> Model | None:
    entries = loaded.manifest.entries_of_kind(kind)
    if not entries:
        return None
    if len(entries) > 1:
        findings.append(
            make_finding(
                FindingCategory.STRUCTURAL,
                Severity.COVERAGE_GAP,
                f"bundle declares {len(entries)} {_LABELS[kind]} artifacts; only "
                f"{entries[0].ref} is used and the others are unreviewed for this role",
                artifact_id=entries[0].artifact_id,
                artifact_version=entries[0].version,
            )
        )
    entry = entries[0]
    payload = loaded.json_of(entry.ref)
    if payload is None:
        findings.append(
            make_finding(
                FindingCategory.PROVENANCE_FAILURE,
                Severity.COVERAGE_GAP,
                f"{_LABELS[kind]} artifact {entry.ref} was not loaded",
                artifact_id=entry.artifact_id,
                artifact_version=entry.version,
            )
        )
        return None
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        findings.append(
            make_finding(
                FindingCategory.STRUCTURAL,
                Severity.COVERAGE_GAP,
                f"{_LABELS[kind]} artifact {entry.ref} does not match its schema: {exc}",
                artifact_id=entry.artifact_id,
                artifact_version=entry.version,
            )
        )
        return None


def _merge_changes(loaded: LoadedBundle, findings: list[Finding]) -> ChangeArtifact:
    merged: list[ChangeRequest] = []
    seen: set[tuple[str, str]] = set()
    for entry in loaded.manifest.entries_of_kind(ArtifactKind.CHANGE_REQUESTS):
        payload = loaded.json_of(entry.ref)
        if payload is None:
            findings.append(
                make_finding(
                    FindingCategory.PROVENANCE_FAILURE,
                    Severity.COVERAGE_GAP,
                    f"change artifact {entry.ref} was not loaded",
                    artifact_id=entry.artifact_id,
                    artifact_version=entry.version,
                )
            )
            continue
        try:
            artifact = ChangeArtifact.model_validate(payload)
        except ValidationError as exc:
            findings.append(
                make_finding(
                    FindingCategory.STRUCTURAL,
                    Severity.COVERAGE_GAP,
                    f"change artifact {entry.ref} does not match its schema: {exc}",
                    artifact_id=entry.artifact_id,
                    artifact_version=entry.version,
                )
            )
            continue
        for change in artifact.changes:
            key = (change.change_id, change.change_version)
            if key in seen:
                findings.append(
                    make_finding(
                        FindingCategory.STRUCTURAL,
                        Severity.COVERAGE_GAP,
                        f"change {change.change_id}@{change.change_version} is declared "
                        "more than once across change artifacts; the later copy is ignored",
                        artifact_id=entry.artifact_id,
                        artifact_version=entry.version,
                    )
                )
                continue
            seen.add(key)
            merged.append(change)
    return ChangeArtifact(changes=tuple(merged))


__all__ = ["ParsedRequirement", "TypedArtifacts", "parse_artifacts"]
