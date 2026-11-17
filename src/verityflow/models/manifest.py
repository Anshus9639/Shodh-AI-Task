from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from verityflow.models.common import FrozenModel

_HEX64 = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")


class ArtifactKind(StrEnum):
    REQUIREMENTS = "requirements"
    DATA_DICTIONARY = "data_dictionary"
    CHANGE_REQUESTS = "change_requests"
    TRACE_MAP = "trace_map"
    SPEC = "spec"
    WORKFLOW = "workflow"
    PYTHON = "python"


class FieldMapEntry(FrozenModel):
    field: str = Field(min_length=1)
    unit: str | None = None


class CodeLink(FrozenModel):
    entrypoint: str = Field(min_length=1)
    param: str = Field(default="ticket", min_length=1)
    field_map: dict[str, FieldMapEntry] = Field(default_factory=dict)
    destination_map: dict[str, str] = Field(default_factory=dict)


class ArtifactEntry(FrozenModel):
    artifact_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    kind: ArtifactKind
    path: str = Field(min_length=1)
    sha256: str
    link: CodeLink | None = None

    @field_validator("sha256")
    @classmethod
    def _normalize_hash(cls, value: str) -> str:
        match = _HEX64.match(value.strip().lower())
        if match is None:
            raise ValueError(
                "sha256 must be 64 hexadecimal characters, optionally prefixed with 'sha256:'"
            )
        return match.group(1)

    @model_validator(mode="after")
    def _check(self) -> ArtifactEntry:
        if self.link is not None and self.kind is not ArtifactKind.PYTHON:
            raise ValueError("link metadata applies to python artifacts only")
        return self

    @property
    def ref(self) -> str:
        return f"{self.artifact_id}@{self.version}"


class ReviewTarget(FrozenModel):
    requirements: tuple[str, ...] = Field(min_length=1)
    spec: str | None = None
    workflow: str | None = None
    code: tuple[str, ...] = ()


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ApprovalRecord(FrozenModel):
    approval_id: str = Field(min_length=1)
    change_id: str = Field(min_length=1)
    change_version: str = Field(min_length=1)
    change_sha256: str
    decision: ApprovalDecision
    approver_id: str = Field(min_length=1)
    approver_role: str = Field(min_length=1)
    recorded_at: datetime
    supersedes: tuple[str, ...] = ()

    @field_validator("change_sha256")
    @classmethod
    def _normalize_hash(cls, value: str) -> str:
        match = _HEX64.match(value.strip().lower())
        if match is None:
            raise ValueError(
                "change_sha256 must be 64 hexadecimal characters, optionally prefixed "
                "with 'sha256:'"
            )
        return match.group(1)


class BundleManifest(FrozenModel):
    bundle_id: str = Field(min_length=1)
    bundle_version: str = Field(min_length=1)
    review_target: ReviewTarget
    artifacts: tuple[ArtifactEntry, ...] = Field(min_length=1)
    approvals: tuple[ApprovalRecord, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> BundleManifest:
        refs = [entry.ref for entry in self.artifacts]
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate artifact id/version pairs")
        if len({approval.approval_id for approval in self.approvals}) != len(self.approvals):
            raise ValueError("duplicate approval ids")
        declared = set(refs)
        wanted = list(self.review_target.requirements)
        wanted.extend(ref for ref in (self.review_target.spec, self.review_target.workflow) if ref)
        wanted.extend(self.review_target.code)
        missing = [ref for ref in wanted if ref not in declared]
        if missing:
            raise ValueError(f"review target references undeclared artifacts: {missing}")
        return self

    def entry(self, ref: str) -> ArtifactEntry | None:
        for entry in self.artifacts:
            if entry.ref == ref:
                return entry
        return None

    def entries_of_kind(self, kind: ArtifactKind) -> tuple[ArtifactEntry, ...]:
        return tuple(entry for entry in self.artifacts if entry.kind is kind)
