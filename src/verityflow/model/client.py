"""Provider-neutral model client interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from verityflow.model.models import ModelCallMetadata, ModelFailureCategory, ModelRequest


@dataclass(frozen=True, slots=True)
class ModelCallResult:
    text: str
    metadata: ModelCallMetadata


class ModelClientError(Exception):
    """A classified transport or provider failure; never a review outcome."""

    def __init__(
        self,
        category: ModelFailureCategory,
        message: str,
        *,
        provider_status: int | None = None,
        metadata: ModelCallMetadata | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.provider_status = provider_status
        self.metadata = metadata


class ModelClient(Protocol):
    provider: str

    def complete(self, request: ModelRequest, prompt: str) -> ModelCallResult:
        """Send one prompt and return raw text plus call metadata."""
        ...


__all__ = ["ModelCallResult", "ModelClient", "ModelClientError"]
