from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TextSpan(FrozenModel):
    kind: Literal["text"] = "text"
    path: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    col_start: int | None = Field(default=None, ge=1)
    col_end: int | None = Field(default=None, ge=1)
    excerpt: str | None = None


class JsonPointerLocation(FrozenModel):
    kind: Literal["json"] = "json"
    path: str = Field(min_length=1)
    pointer: str
    excerpt: str | None = None


Location = Annotated[TextSpan | JsonPointerLocation, Field(discriminator="kind")]


class ArtifactRef(FrozenModel):
    artifact_id: str = Field(min_length=1)
    version: str = Field(min_length=1)

    def __str__(self) -> str:
        return f"{self.artifact_id}@{self.version}"

    @classmethod
    def parse(cls, text: str) -> ArtifactRef:
        artifact_id, sep, version = text.partition("@")
        if not sep or not artifact_id or not version:
            raise ValueError(f"expected 'ID@version', got {text!r}")
        return cls(artifact_id=artifact_id, version=version)
