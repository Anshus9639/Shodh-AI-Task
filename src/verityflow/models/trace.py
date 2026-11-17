from __future__ import annotations

from pydantic import Field

from verityflow.models.common import FrozenModel


class TraceMapArtifact(FrozenModel):
    """Optional correspondence hints. Hints never establish equivalence."""

    trace_map_id: str = Field(default="TRACE", min_length=1)
    version: str = Field(default="1", min_length=1)
    requirement_to_spec: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    requirement_to_workflow: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    requirement_to_code: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    supplemental: dict[str, tuple[str, ...]] = Field(default_factory=dict)
