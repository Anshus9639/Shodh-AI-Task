from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaError
from pydantic import ValidationError

from verityflow.ingest.artifacts import parse_artifacts
from verityflow.ingest.lineindex import LineIndex
from verityflow.ingest.loader import load_bundle
from verityflow.models.common import ArtifactRef, TextSpan
from verityflow.models.manifest import (
    ApprovalDecision,
    ApprovalRecord,
    ArtifactEntry,
    ArtifactKind,
    BundleManifest,
    CodeLink,
    ReviewTarget,
)
from verityflow.models.requirements import AnchoredRequirement, RequirementsSource

ROOT = Path(__file__).resolve().parent.parent.parent
DEV = ROOT / "fixtures" / "dev"
SCHEMAS = ROOT / "schemas"

_HASH = "a" * 64


def _entry(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "artifact_id": "A",
        "version": "1",
        "kind": "requirements",
        "path": "requirements.txt",
        "sha256": _HASH,
    }
    payload.update(overrides)
    return payload


def _schema(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMAS / f"{name}.schema.json").read_text("utf-8")))


def test_artifact_references_require_non_empty_id_and_version() -> None:
    assert ArtifactRef.parse("REQ@1.0") == ArtifactRef(artifact_id="REQ", version="1.0")
    for text in ("REQ", "REQ@", "@1.0", ""):
        with pytest.raises(ValueError):
            ArtifactRef.parse(text)
    for field in ("artifact_id", "version"):
        with pytest.raises(ValidationError):
            ArtifactEntry.model_validate(_entry(**{field: ""}))


def test_manifest_rejects_duplicate_artifact_versions() -> None:
    manifest = {
        "bundle_id": "B",
        "bundle_version": "1",
        "review_target": {"requirements": ["A@1"]},
        "artifacts": [_entry(), _entry()],
    }
    with pytest.raises(ValidationError):
        BundleManifest.model_validate(manifest)


def test_every_requirement_anchor_resolves_against_its_source() -> None:
    loaded = load_bundle(DEV / "B01_starter")
    typed = parse_artifacts(loaded)
    requirements_entry = loaded.manifest.entries_of_kind(ArtifactKind.REQUIREMENTS)[0]
    dictionary_entry = loaded.manifest.entries_of_kind(ArtifactKind.DATA_DICTIONARY)[0]
    text = loaded.text_of(requirements_entry.ref)
    assert text is not None
    document = typed.requirements[0].document
    index = LineIndex(text)

    source = RequirementsSource(
        requirements_id=requirements_entry.artifact_id,
        version=requirements_entry.version,
        path=requirements_entry.path,
        sha256=requirements_entry.sha256,
        dictionary_ref=ArtifactRef(
            artifact_id=dictionary_entry.artifact_id, version=dictionary_entry.version
        ),
        requirements=tuple(
            AnchoredRequirement(
                requirement_id=rule.rule_ref,
                source_ref=ArtifactRef(
                    artifact_id=requirements_entry.artifact_id,
                    version=requirements_entry.version,
                ),
                anchor=rule.anchor,
                source_text=rule.source_text,
                destination=rule.destination,
            )
            for rule in document.rules
        ),
    )
    assert len(source.requirements) == 4
    for requirement in source.requirements:
        assert index.check_span(requirement.anchor, expected_path=source.path) is None
        assert requirement.source_text in index.excerpt(
            requirement.anchor.line_start, requirement.anchor.line_end
        )


def test_anchor_checks_detect_a_tampered_span() -> None:
    loaded = load_bundle(DEV / "B01_starter")
    typed = parse_artifacts(loaded)
    entry = loaded.manifest.entries_of_kind(ArtifactKind.REQUIREMENTS)[0]
    document = typed.requirements[0].document
    index = LineIndex(loaded.text_of(entry.ref) or "")
    anchor = document.rules[0].anchor
    assert index.check_span(anchor, expected_path=entry.path) is None
    tampered = anchor.model_copy(update={"line_end": index.line_count + 5})
    assert index.check_span(tampered, expected_path=entry.path) is not None
    assert index.check_span(anchor, expected_path="other.txt") is not None


def test_requirements_source_rejects_mismatched_anchors() -> None:
    with pytest.raises(ValidationError):
        RequirementsSource(
            requirements_id="REQ",
            version="1",
            path="requirements.txt",
            sha256=_HASH,
            dictionary_ref=ArtifactRef(artifact_id="DICT", version="1"),
            requirements=(
                AnchoredRequirement(
                    requirement_id="R1",
                    source_ref=ArtifactRef(artifact_id="OTHER", version="1"),
                    anchor=TextSpan(path="requirements.txt", line_start=1, line_end=1),
                    source_text="R1. x -> y.",
                ),
            ),
        )


def test_approval_metadata_requires_a_hash_and_normalizes_it() -> None:
    payload = {
        "approval_id": "A1",
        "change_id": "C1",
        "change_version": "1",
        "change_sha256": "SHA256:" + _HASH.upper(),
        "decision": "approve",
        "approver_id": "alice",
        "approver_role": "policy_owner",
        "recorded_at": "2026-02-01T10:00:00Z",
    }
    approval = ApprovalRecord.model_validate(payload)
    assert approval.change_sha256 == _HASH
    assert approval.decision is ApprovalDecision.APPROVE
    for field in ("approval_id", "change_id", "change_version", "approver_id", "approver_role"):
        with pytest.raises(ValidationError):
            ApprovalRecord.model_validate({**payload, field: ""})
    with pytest.raises(ValidationError):
        ApprovalRecord.model_validate({**payload, "change_sha256": "not-a-hash"})


def test_recorded_approvals_bind_to_the_declared_change_payload() -> None:
    loaded = load_bundle(DEV / "B03_approved_change")
    typed = parse_artifacts(loaded)
    assert typed.changes.changes
    change = typed.changes.changes[0]
    approval = loaded.manifest.approvals[0]
    assert approval.change_id == change.change_id
    assert approval.change_version == change.change_version
    assert approval.change_sha256 == change.content_hash()


def test_python_artifacts_require_link_provenance() -> None:
    with pytest.raises(ValidationError):
        ArtifactEntry.model_validate(_entry(kind="python"))
    entry = ArtifactEntry.model_validate(
        _entry(
            kind="python",
            path="rules.py",
            link={"entrypoint": "route", "param": "ticket", "destination_map": {"a": "b"}},
        )
    )
    assert entry.link == CodeLink(entrypoint="route", param="ticket", destination_map={"a": "b"})
    with pytest.raises(ValidationError):
        ArtifactEntry.model_validate(_entry(link={"entrypoint": "route"}))
    with pytest.raises(ValidationError):
        ArtifactEntry.model_validate(_entry(link={"entrypoint": ""}))
    with pytest.raises(ValidationError):
        BundleManifest.model_validate(
            {
                "bundle_id": "B",
                "bundle_version": "1",
                "review_target": {"requirements": ["A@1"], "code": ["A@1"]},
                "artifacts": [_entry(kind="python", path="rules.py")],
            }
        )


def test_review_target_must_reference_declared_artifacts() -> None:
    with pytest.raises(ValidationError):
        BundleManifest.model_validate(
            {
                "bundle_id": "B",
                "bundle_version": "1",
                "review_target": ReviewTarget(requirements=("MISSING@1",)).model_dump(),
                "artifacts": [_entry()],
            }
        )


_NOT_TRUE = {"kind": "not_true", "item": {"kind": "const", "value": True}}


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (
            "spec",
            {
                "spec_id": "S",
                "version": "1",
                "rules": [{"id": "R", "priority": 1, "when": _NOT_TRUE, "destination": "d"}],
            },
        ),
        (
            "spec_rule",
            {"id": "R", "priority": 1, "when": _NOT_TRUE, "destination": "d"},
        ),
        (
            "workflow",
            {
                "workflow_id": "W",
                "version": "1",
                "start": "n",
                "nodes": [
                    {
                        "id": "n",
                        "version": "1",
                        "kind": "decision",
                        "branches": [
                            {
                                "branch_id": "b",
                                "version": "1",
                                "condition": _NOT_TRUE,
                                "target": "t",
                            }
                        ],
                    },
                    {"id": "t", "version": "1", "kind": "terminal", "destination": "d"},
                ],
            },
        ),
        (
            "workflow_branch",
            {"branch_id": "b", "version": "1", "condition": _NOT_TRUE, "target": "t"},
        ),
        (
            "data_dictionary",
            {
                "fields": [{"name": "category", "type": "string"}],
                "named_predicates": {"p": _NOT_TRUE},
            },
        ),
    ],
)
def test_exported_artifact_schemas_reject_internal_guards(schema: str, payload: object) -> None:
    with pytest.raises(SchemaError):
        _schema(schema).validate(payload)


def test_standalone_contract_schemas_are_exported() -> None:
    for name in (
        "requirements_source",
        "anchored_requirement",
        "spec_rule",
        "spec_default",
        "workflow_node",
        "workflow_branch",
        "artifact_entry",
        "code_link",
        "approval_record",
    ):
        assert (SCHEMAS / f"{name}.schema.json").is_file(), name
