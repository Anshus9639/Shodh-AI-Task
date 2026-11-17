from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, model_validator

from verityflow.models.common import ArtifactRef, FrozenModel, TextSpan

_HEX64 = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$")


def _normalize_sha256(value: str) -> str:
    match = _HEX64.match(value.strip().lower())
    if match is None:
        raise ValueError(
            "sha256 must be 64 hexadecimal characters, optionally prefixed with 'sha256:'"
        )
    return match.group(1)


Sha256 = Annotated[str, AfterValidator(_normalize_sha256)]


class AnchoredRequirement(FrozenModel):
    """One requirement statement tied to an exact span of its source artifact.

    The anchor is evidence, not a claim: ``source_ref`` identifies the declared
    artifact version and ``anchor`` must resolve against that artifact's text.
    """

    requirement_id: str = Field(min_length=1)
    source_ref: ArtifactRef
    anchor: TextSpan
    source_text: str = Field(min_length=1)
    destination: str | None = None


class RequirementsSource(FrozenModel):
    """Declared contract for a plain-text requirements artifact and its anchors.

    Requirement files are stored as UTF-8 text; this model is the standalone
    projection of the manifest entry plus every parsed, anchored requirement.
    """

    requirements_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    path: str = Field(min_length=1)
    sha256: Sha256
    encoding: Literal["utf-8"] = "utf-8"
    dictionary_ref: ArtifactRef
    requirements: tuple[AnchoredRequirement, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> RequirementsSource:
        source = ArtifactRef(artifact_id=self.requirements_id, version=self.version)
        ids = [requirement.requirement_id for requirement in self.requirements]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate requirement ids")
        for requirement in self.requirements:
            if requirement.source_ref != source:
                raise ValueError(
                    f"requirement {requirement.requirement_id!r} cites "
                    f"{requirement.source_ref}, expected {source}"
                )
            if requirement.anchor.path != self.path:
                raise ValueError(
                    f"requirement {requirement.requirement_id!r} anchor path "
                    f"{requirement.anchor.path!r} does not match {self.path!r}"
                )
        return self


__all__ = ["AnchoredRequirement", "RequirementsSource", "Sha256"]
