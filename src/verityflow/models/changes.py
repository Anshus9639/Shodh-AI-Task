from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import ConfigDict, Field, model_validator

from verityflow.ir.model import NumericLit
from verityflow.models.common import FrozenModel


class ChangeOperation(StrEnum):
    REPLACE_THRESHOLD = "replace_threshold"
    REPLACE_OPERATOR = "replace_operator"
    REPLACE_DESTINATION = "replace_destination"
    REPLACE_CONDITION = "replace_condition"
    REMOVE_RULE = "remove_rule"
    ADD_RULE = "add_rule"


class ChangePayload(FrozenModel):
    field: str | None = None
    value: NumericLit | None = None
    unit: str | None = None
    op: str | None = None
    destination: str | None = None
    text: str | None = None
    after: str | None = None


class ChangeRequest(FrozenModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    change_id: str = Field(min_length=1)
    change_version: str = Field(min_length=1)
    base_artifact: str = Field(min_length=1)
    base_version: str = Field(min_length=1)
    target_rule: str | None = None
    operation: ChangeOperation
    from_payload: ChangePayload | None = Field(default=None, alias="from")
    to_payload: ChangePayload | None = Field(default=None, alias="to")
    sequence: int | None = None
    rationale: str = ""

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")

    def content_hash(self) -> str:
        canonical = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ChangeArtifact(FrozenModel):
    changes: tuple[ChangeRequest, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> ChangeArtifact:
        keys = [(change.change_id, change.change_version) for change in self.changes]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate (change_id, change_version) pairs")
        return self

    def by_id(self, change_id: str, change_version: str) -> ChangeRequest | None:
        for change in self.changes:
            if change.change_id == change_id and change.change_version == change_version:
                return change
        return None
