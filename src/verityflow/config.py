from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from verityflow.model.models import ModelMode


class Limits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_artifact_bytes: int = Field(default=200_000, gt=0)
    max_artifacts: int = Field(default=64, gt=0)
    max_cells: int = Field(default=50_000, gt=0)
    max_candidate_policies: int = Field(default=8, gt=0)
    mapping_max_signature_cells: int = Field(default=4096, gt=0)
    interpreter_max_steps: int = Field(default=10_000, gt=0)
    interpreter_max_ast_nodes: int = Field(default=20_000, gt=0)
    interpreter_max_depth: int = Field(default=50, gt=0)


class ApprovalPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    authorized_roles: tuple[str, ...] = ("policy_owner",)


class ModelSettings(BaseModel):
    """Supplementary model analysis; off by default and never authoritative."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: ModelMode = ModelMode.OFF
    model: str = Field(default="gemini-3.5-flash-lite", min_length=1)
    prompt_version: str = Field(default="reviewer-v1", min_length=1)
    timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    max_retries: int = Field(default=2, ge=0, le=5)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_output_tokens: int = Field(default=2048, ge=1, le=8192)
    max_request_bytes: int = Field(default=262_144, ge=1024)
    max_response_bytes: int = Field(default=131_072, ge=1024)
    max_observations: int = Field(default=32, ge=1, le=256)
    fake_scenario: str = Field(default="valid", min_length=1)
    cache_enabled: bool = True


class Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    limits: Limits = Field(default_factory=Limits)
    approvals: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    model: ModelSettings = Field(default_factory=ModelSettings)


class ConfigError(ValueError):
    """The configuration file cannot be read or does not match the schema."""


def load_config(path: Path | None = None) -> Config:
    """Load a TOML configuration; unknown keys are rejected, never ignored."""
    if path is None:
        return Config()
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read configuration {path}: {exc}") from exc
    try:
        payload = tomllib.loads(content)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"configuration {path} is not valid TOML: {exc}") from exc
    try:
        return Config.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"configuration {path} does not match the schema: {exc}") from exc


__all__ = [
    "ApprovalPolicy",
    "Config",
    "ConfigError",
    "Limits",
    "ModelSettings",
    "load_config",
]
