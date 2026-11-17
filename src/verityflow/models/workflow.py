from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from verityflow.ir.model import Expr
from verityflow.models.common import FrozenModel


class NodeKind(StrEnum):
    DECISION = "decision"
    TERMINAL = "terminal"


class Branch(FrozenModel):
    branch_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    condition: Expr | None = None
    target: str = Field(min_length=1)


class WorkflowNode(FrozenModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    kind: NodeKind
    implements: tuple[str, ...] = ()
    branches: tuple[Branch, ...] = ()
    destination: str | None = None

    @model_validator(mode="after")
    def _check(self) -> WorkflowNode:
        if self.kind is NodeKind.DECISION:
            if not self.branches:
                raise ValueError(f"decision node {self.id!r} has no branches")
            if self.destination is not None:
                raise ValueError(f"decision node {self.id!r} cannot declare a destination")
            else_branches = [branch for branch in self.branches if branch.condition is None]
            if len(else_branches) > 1:
                raise ValueError(f"node {self.id!r} has more than one else-branch")
            if else_branches and self.branches[-1].condition is not None:
                raise ValueError(f"node {self.id!r}: else-branch must be last")
            ids = [branch.branch_id for branch in self.branches]
            if len(set(ids)) != len(ids):
                raise ValueError(f"node {self.id!r} has duplicate branch ids")
        else:
            if self.destination is None:
                raise ValueError(f"terminal node {self.id!r} requires a destination")
            if self.branches:
                raise ValueError(f"terminal node {self.id!r} cannot have branches")
        return self


class WorkflowArtifact(FrozenModel):
    workflow_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    start: str = Field(min_length=1)
    nodes: tuple[WorkflowNode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> WorkflowArtifact:
        ids = [node.id for node in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate workflow node ids")
        if self.start not in ids:
            raise ValueError(f"start node {self.start!r} is not declared")
        return self

    def node(self, node_id: str) -> WorkflowNode | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None
