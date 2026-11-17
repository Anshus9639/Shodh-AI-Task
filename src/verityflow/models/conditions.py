from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field

from verityflow.ir.model import (
    And,
    Cmp,
    Const,
    Eq,
    Expr,
    In,
    Missing,
    Ne,
    NotIn,
    NotNode,
    Or,
)
from verityflow.models.common import FrozenModel


class ExternalAnd(FrozenModel):
    kind: Literal["and"] = "and"
    items: tuple[ExternalCondition, ...] = Field(min_length=2)


class ExternalOr(FrozenModel):
    kind: Literal["or"] = "or"
    items: tuple[ExternalCondition, ...] = Field(min_length=2)


class ExternalNot(FrozenModel):
    kind: Literal["not"] = "not"
    item: ExternalCondition


ExternalCondition: TypeAlias = Annotated[  # noqa: UP040 - Pydantic requires a runtime alias
    Cmp | Eq | Ne | In | NotIn | Missing | ExternalAnd | ExternalOr | ExternalNot | Const,
    Field(discriminator="kind"),
]

_EXTERNAL_NAMESPACE = {"ExternalCondition": ExternalCondition}
for _model in (ExternalAnd, ExternalOr, ExternalNot):
    _model.model_rebuild(force=True, _types_namespace=_EXTERNAL_NAMESPACE)


def to_ir(condition: ExternalCondition) -> Expr:
    """Translate an externally supplied condition into the internal IR.

    External artifacts cannot express the internal ``not_true`` first-match
    guard, so this conversion only re-keys composite nodes and leaves leaves
    unchanged.
    """
    if isinstance(condition, ExternalAnd):
        return And(items=tuple(to_ir(item) for item in condition.items))
    if isinstance(condition, ExternalOr):
        return Or(items=tuple(to_ir(item) for item in condition.items))
    if isinstance(condition, ExternalNot):
        return NotNode(item=to_ir(condition.item))
    return condition
