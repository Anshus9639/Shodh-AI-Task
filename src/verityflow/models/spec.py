from __future__ import annotations

from pydantic import Field, model_validator

from verityflow.ir.model import Expr
from verityflow.models.common import FrozenModel


class SpecRule(FrozenModel):
    id: str = Field(min_length=1)
    priority: int
    implements: tuple[str, ...] = ()
    when: Expr
    destination: str = Field(min_length=1)


class SpecDefault(FrozenModel):
    destination: str = Field(min_length=1)
    implements: tuple[str, ...] = ()


class SpecArtifact(FrozenModel):
    spec_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    rules: tuple[SpecRule, ...] = ()
    default: SpecDefault | None = None

    @model_validator(mode="after")
    def _check(self) -> SpecArtifact:
        ids = [rule.id for rule in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate spec rule ids")
        return self
