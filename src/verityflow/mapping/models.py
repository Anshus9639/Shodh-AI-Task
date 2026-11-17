from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from verityflow.ir.model import Expr
from verityflow.models.common import FrozenModel
from verityflow.models.report import Finding


class ElementKind(StrEnum):
    REQUIREMENT = "requirement"
    SPEC_RULE = "spec_rule"
    SPEC_DEFAULT = "spec_default"
    WORKFLOW_PATH = "workflow_path"


class SemanticElement(FrozenModel):
    """ID-free semantic view of one element from a reviewed artifact."""

    element_id: str = Field(min_length=1)
    kind: ElementKind
    condition: Expr
    destination: str = Field(min_length=1)
    precedence: int = Field(ge=0)
    exception_of: str | None = None
    detail: str | None = None


class Extraction(FrozenModel):
    elements: tuple[SemanticElement, ...] = ()
    findings: tuple[Finding, ...] = ()
    sound: bool = True


class SignatureOutcome(FrozenModel):
    equal: bool | None
    states: int = 0
    reason: str | None = None


class MatchMethod(StrEnum):
    INFERRED = "inferred"
    HINT = "hint"
    PRECEDENCE = "precedence"


class MappingMatch(FrozenModel):
    requirement_ref: str
    element_id: str
    element_kind: ElementKind
    method: MatchMethod
    states: int = 0


class MappingResult(FrozenModel):
    artifact: str
    matches: tuple[MappingMatch, ...] = ()
    unmatched_requirements: tuple[str, ...] = ()
    unmatched_elements: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    complete: bool = True


__all__ = [
    "ElementKind",
    "Extraction",
    "MappingMatch",
    "MappingResult",
    "MatchMethod",
    "SemanticElement",
    "SignatureOutcome",
]
