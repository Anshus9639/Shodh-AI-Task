"""Optional Gemini adapter over the standard library.

Disabled unless the caller explicitly selects ``--model gemini`` (or configures
``[model] mode = "gemini"``). The API key is read from the environment and is
never written to logs, reports or the repository. Tests never call this adapter
against the network; the ordinary fake-client path needs no credentials.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from verityflow.config import ModelSettings
from verityflow.model.client import ModelCallResult, ModelClientError
from verityflow.model.models import (
    ModelCallMetadata,
    ModelFailureCategory,
    ModelRequest,
    ModelUsage,
)
from verityflow.model.prompt import PAYLOAD_OPEN

API_KEY_ENV = "VERITYFLOW_GEMINI_API_KEY"
API_KEY_FALLBACK_ENV = "GEMINI_API_KEY"
MODEL_ENV = "VERITYFLOW_GEMINI_MODEL"
ENDPOINT_ENV = "VERITYFLOW_GEMINI_ENDPOINT"
DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"

_AUTH_STATUSES = frozenset({401, 403})
_TRANSPORT_STATUSES = frozenset({408, 504})


class GeminiModelClient:
    provider = "gemini"

    def __init__(self, settings: ModelSettings) -> None:
        self._api_key = os.environ.get(API_KEY_ENV) or os.environ.get(API_KEY_FALLBACK_ENV)
        self._model = os.environ.get(MODEL_ENV) or settings.model
        self._endpoint = os.environ.get(ENDPOINT_ENV, DEFAULT_ENDPOINT)
        self._timeout = settings.timeout_seconds
        self._temperature = settings.temperature
        self._max_output_tokens = settings.max_output_tokens
        self._max_read_bytes = settings.max_response_bytes + 1

    def complete(self, request: ModelRequest, prompt: str) -> ModelCallResult:
        if not self._api_key:
            raise ModelClientError(
                ModelFailureCategory.AUTHENTICATION_FAILURE,
                f"no Gemini API key is configured; set {API_KEY_ENV} or {API_KEY_FALLBACK_ENV}",
            )
        head, separator, tail = prompt.partition(PAYLOAD_OPEN)
        if not separator:
            raise ModelClientError(
                ModelFailureCategory.PROMPT_CONSTRUCTION,
                "rendered prompt has no delimited artifact payload",
            )
        body = json.dumps(
            {
                "systemInstruction": {"parts": [{"text": head.rstrip()}]},
                "contents": [{"parts": [{"text": PAYLOAD_OPEN + tail}]}],
                "generationConfig": {
                    "temperature": self._temperature,
                    "maxOutputTokens": self._max_output_tokens,
                    "responseMimeType": "application/json",
                },
            }
        ).encode("utf-8")
        url = (
            f"{self._endpoint.rstrip('/')}/models/"
            f"{urllib.parse.quote(self._model, safe='')}:generateContent"
        )
        http_request = urllib.request.Request(  # noqa: S310 - fixed https endpoint
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(  # noqa: S310 - fixed https endpoint
                http_request, timeout=self._timeout
            ) as response:
                status = response.status
                request_id = response.headers.get("x-goog-request-id") or response.headers.get(
                    "x-request-id"
                )
                raw = response.read(self._max_read_bytes)
        except urllib.error.HTTPError as exc:
            raise _http_error(exc.code) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ModelClientError(
                    ModelFailureCategory.TIMEOUT, "Gemini request timed out"
                ) from exc
            raise ModelClientError(
                ModelFailureCategory.CONNECTION_FAILURE,
                f"Gemini connection failed: {type(exc.reason).__name__}",
            ) from exc
        except TimeoutError as exc:
            raise ModelClientError(
                ModelFailureCategory.TIMEOUT, "Gemini request timed out"
            ) from exc
        except OSError as exc:
            raise ModelClientError(
                ModelFailureCategory.CONNECTION_FAILURE,
                f"Gemini connection failed: {type(exc).__name__}",
            ) from exc
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        text = raw.decode("utf-8", errors="replace")
        generated, metadata = _extract(text, status, request_id, latency_ms)
        return ModelCallResult(text=generated, metadata=metadata)


def _extract(
    text: str, status: int, request_id: str | None, latency_ms: int
) -> tuple[str, ModelCallMetadata]:
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelClientError(
            ModelFailureCategory.MALFORMED_JSON,
            f"Gemini returned a non-JSON envelope: {exc}",
            provider_status=status,
        ) from exc
    if not isinstance(envelope, dict):
        raise ModelClientError(
            ModelFailureCategory.SCHEMA_VALIDATION,
            "Gemini response envelope is not a JSON object",
            provider_status=status,
        )
    candidates = envelope.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ModelClientError(
            ModelFailureCategory.SERVER_ERROR,
            "Gemini response contains no candidates",
            provider_status=status,
        )
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise ModelClientError(
            ModelFailureCategory.SCHEMA_VALIDATION,
            "Gemini candidate is not a JSON object",
            provider_status=status,
        )
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise ModelClientError(
            ModelFailureCategory.SCHEMA_VALIDATION,
            "Gemini candidate has no content parts",
            provider_status=status,
        )
    generated = "".join(
        part.get("text", "") for part in parts if isinstance(part, dict) and "text" in part
    )
    usage = _usage(envelope.get("usageMetadata"))
    model_version = envelope.get("modelVersion")
    metadata = ModelCallMetadata(
        provider="gemini",
        model=str(candidate.get("modelVersion") or model_version or "unknown"),
        provider_request_id=request_id,
        model_version=None if model_version is None else str(model_version),
        finish_reason=(
            None if candidate.get("finishReason") is None else str(candidate["finishReason"])
        ),
        usage=usage,
        latency_ms=latency_ms,
        provider_status=status,
        synthetic=False,
    )
    return generated, metadata


def _usage(payload: object) -> ModelUsage | None:
    if not isinstance(payload, dict):
        return None
    return ModelUsage(
        input_tokens=_count(payload.get("promptTokenCount")),
        output_tokens=_count(payload.get("candidatesTokenCount")),
        total_tokens=_count(payload.get("totalTokenCount")),
        synthetic=False,
    )


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _http_error(status: int) -> ModelClientError:
    if status in _AUTH_STATUSES:
        return ModelClientError(
            ModelFailureCategory.AUTHENTICATION_FAILURE,
            f"Gemini rejected the credentials (HTTP {status})",
            provider_status=status,
        )
    if status == 429:
        return ModelClientError(
            ModelFailureCategory.RATE_LIMIT,
            "Gemini rate limit reached (HTTP 429)",
            provider_status=status,
        )
    if status in _TRANSPORT_STATUSES or 500 <= status < 600:
        return ModelClientError(
            ModelFailureCategory.SERVER_ERROR,
            f"Gemini server error (HTTP {status})",
            provider_status=status,
        )
    return ModelClientError(
        ModelFailureCategory.PROMPT_CONSTRUCTION,
        f"Gemini rejected the request (HTTP {status})",
        provider_status=status,
    )


__all__ = [
    "API_KEY_ENV",
    "API_KEY_FALLBACK_ENV",
    "DEFAULT_ENDPOINT",
    "ENDPOINT_ENV",
    "MODEL_ENV",
    "GeminiModelClient",
]
