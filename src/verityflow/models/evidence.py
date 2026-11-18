from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from verityflow.models.common import (
    FrozenModel,
    JsonPointerLocation,
    Location,
    TextSpan,
)


class EvidenceRole(StrEnum):
    SOURCE_REQUIREMENT = "SOURCE_REQUIREMENT"
    SPECIFICATION = "SPECIFICATION"
    WORKFLOW_BRANCH = "WORKFLOW_BRANCH"
    PYTHON_CODE = "PYTHON_CODE"
    APPROVAL_RECORD = "APPROVAL_RECORD"
    EXECUTION_TRACE = "EXECUTION_TRACE"


class AnchorType(StrEnum):
    SOURCE_LINE = "source_line"
    LINE_RANGE = "line_range"
    JSON_POINTER = "json_pointer"
    PARAGRAPH = "paragraph"
    NAMED_BLOCK = "named_block"


class EvidenceReference(FrozenModel):
    """A validated pointer back into one artifact revision.

    ``anchor_value`` is the raw anchor (a JSON Pointer or a line/block address).
    ``excerpt`` is always drawn from the artifact by the source catalog; it is
    never composed by a verifier.
    """

    artifact_id: str = Field(min_length=1)
    artifact_version: str = Field(min_length=1)
    anchor_type: AnchorType
    anchor_value: str
    path: str = Field(min_length=1)
    source_hash: str = Field(min_length=1)
    excerpt: str
    evidence_role: EvidenceRole
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    col_start: int | None = Field(default=None, ge=1)
    col_end: int | None = Field(default=None, ge=1)

    @property
    def artifact_ref(self) -> str:
        return f"{self.artifact_id}@{self.artifact_version}"

    def to_location(self) -> Location:
        if self.anchor_type is AnchorType.JSON_POINTER:
            return JsonPointerLocation(
                path=self.path, pointer=self.anchor_value, excerpt=self.excerpt
            )
        if self.line_start is None or self.line_end is None:
            raise ValueError("text evidence requires a line range")
        return TextSpan(
            path=self.path,
            line_start=self.line_start,
            line_end=self.line_end,
            col_start=self.col_start,
            col_end=self.col_end,
            excerpt=self.excerpt,
        )


__all__ = ["AnchorType", "EvidenceReference", "EvidenceRole"]
