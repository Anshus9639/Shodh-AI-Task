from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Limits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_artifact_bytes: int = Field(default=200_000, gt=0)
    max_artifacts: int = Field(default=64, gt=0)
    max_cells: int = Field(default=50_000, gt=0)
    max_candidate_policies: int = Field(default=8, gt=0)
    interpreter_max_steps: int = Field(default=10_000, gt=0)
    interpreter_max_ast_nodes: int = Field(default=20_000, gt=0)
    interpreter_max_depth: int = Field(default=50, gt=0)


class ApprovalPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    authorized_roles: tuple[str, ...] = ("policy_owner",)


class Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    limits: Limits = Field(default_factory=Limits)
    approvals: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
