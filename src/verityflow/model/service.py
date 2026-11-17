"""Supplementary model analysis orchestration.

The deterministic :class:`ReviewReport` is the only authority. This service
builds a bounded request, calls one provider-neutral client with bounded
retries, validates and classifies the response, and records provenance. It never
edits the report; every analysis copies the deterministic status verbatim.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from verityflow.config import Config, ModelSettings
from verityflow.ingest.loader import LoadedBundle
from verityflow.model.client import ModelCallResult, ModelClient, ModelClientError
from verityflow.model.fake import FakeModelClient
from verityflow.model.models import (
    ClassifiedObservation,
    FindingSummary,
    ModelAnalysis,
    ModelAnalysisStatus,
    ModelFailure,
    ModelFailureCategory,
    ModelMode,
    ModelRequest,
    ModelRequestProvenance,
    ModelResponseProvenance,
    Observation,
    ObservationClassification,
    RejectedObservation,
)
from verityflow.model.prompt import PromptError, render_prompt
from verityflow.model.request import RequestBuildError, build_model_request
from verityflow.model.validate import validate_response
from verityflow.verify.models import ReviewReport

_RETRYABLE = frozenset(
    {
        ModelFailureCategory.TIMEOUT,
        ModelFailureCategory.CONNECTION_FAILURE,
        ModelFailureCategory.RATE_LIMIT,
        ModelFailureCategory.SERVER_ERROR,
    }
)

_CACHE: dict[str, ModelAnalysis] = {}

_UNSUPPORTED_CATEGORIES = frozenset(
    {"candidate_mapping", "ambiguous_requirement", "suggested_test"}
)


def clear_model_cache() -> None:
    _CACHE.clear()


def build_client(settings: ModelSettings) -> ModelClient:
    if settings.mode is ModelMode.FAKE:
        return FakeModelClient(settings.fake_scenario)
    if settings.mode is ModelMode.GEMINI:
        from verityflow.model.gemini import GeminiModelClient

        return GeminiModelClient(settings)
    raise ValueError("model mode is off; no client is available")


def run_model_analysis(
    loaded: LoadedBundle,
    report: ReviewReport,
    *,
    config: Config,
    client: ModelClient | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ModelAnalysis:
    settings = config.model
    if settings.mode is ModelMode.OFF:
        raise ValueError("model mode is off; supplementary analysis is disabled")
    active_client = client if client is not None else build_client(settings)
    timestamp = (clock or _now)().isoformat(timespec="seconds")
    try:
        request = build_model_request(loaded, report, settings)
    except RequestBuildError as exc:
        return _skipped(settings, report, exc.category, str(exc), timestamp)
    try:
        prompt = render_prompt(request)
    except PromptError as exc:
        return _terminal(
            settings,
            report,
            _request_provenance(settings, "", timestamp),
            ModelFailure.from_category(ModelFailureCategory.PROMPT_CONSTRUCTION, str(exc)),
            synthetic=_is_synthetic(active_client, None),
        )
    request_provenance = _request_provenance(settings, prompt, timestamp)
    input_bytes = len(prompt.encode("utf-8"))
    if input_bytes > settings.max_request_bytes:
        return _skipped(
            settings,
            report,
            ModelFailureCategory.REQUEST_TOO_LARGE,
            f"constructed prompt is {input_bytes} bytes, exceeding max_request_bytes="
            f"{settings.max_request_bytes}; model analysis is skipped rather than truncating "
            "mandatory context",
            timestamp,
            request=request_provenance,
        )

    cache_key = _cache_key(request, settings)
    if settings.cache_enabled:
        cached = _CACHE.get(cache_key)
        if cached is not None and cached.deterministic_status == report.status.value:
            return cached.model_copy(update={"cache_hit": True})

    outcome = _call_with_retries(active_client, request, prompt, settings)
    synthetic = _is_synthetic(active_client, outcome.result)
    if outcome.failure is not None:
        analysis = _failed(
            settings,
            report,
            request_provenance,
            outcome.failure,
            synthetic=synthetic,
            response=ModelResponseProvenance(
                latency_ms=outcome.latency_ms,
                retry_count=outcome.retry_count,
                provider_status=outcome.provider_status,
                failure_category=outcome.failure.category.value,
                synthetic=synthetic,
            ),
        )
        return analysis

    if outcome.result is None:
        raise RuntimeError("a completed call outcome must carry a result")
    text = outcome.result.text
    metadata = outcome.result.metadata
    response_provenance = ModelResponseProvenance(
        provider_request_id=metadata.provider_request_id,
        model_version=metadata.model_version,
        response_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        finish_reason=metadata.finish_reason,
        usage=metadata.usage,
        latency_ms=outcome.latency_ms,
        retry_count=outcome.retry_count,
        provider_status=metadata.provider_status,
        synthetic=synthetic,
    )
    validation = validate_response(
        text,
        request,
        max_response_bytes=settings.max_response_bytes,
        max_observations=settings.max_observations,
    )
    if validation.failure is not None or validation.response is None:
        failure = validation.failure or ModelFailure.from_category(
            ModelFailureCategory.SCHEMA_VALIDATION, "response was not accepted"
        )
        response_provenance = response_provenance.model_copy(
            update={"failure_category": failure.category.value}
        )
        return _failed(
            settings,
            report,
            request_provenance,
            failure,
            synthetic=synthetic,
            response=response_provenance,
            rejected=validation.rejected,
        )

    assisted, unverified = _classify(validation.response.observations, report)
    failure_category = validation.rejected[0].reason.value if validation.rejected else None
    response_provenance = response_provenance.model_copy(
        update={"failure_category": failure_category}
    )
    analysis = ModelAnalysis(
        status=ModelAnalysisStatus.COMPLETED,
        provider=active_client.provider,
        model=settings.model,
        prompt_version=settings.prompt_version,
        synthetic=synthetic,
        deterministic_status=report.status.value,
        summary=validation.response.analysis_summary,
        confirmed_findings=_finding_summaries(report),
        assisted_observations=tuple(assisted),
        unverified_observations=tuple(unverified),
        rejected_observations=validation.rejected,
        ambiguities=validation.response.ambiguities,
        suggested_tests=validation.response.suggested_tests,
        request=request_provenance,
        response=response_provenance,
        cache_key=cache_key,
    )
    if settings.cache_enabled:
        _CACHE[cache_key] = analysis
    return analysis


@dataclass(frozen=True, slots=True)
class _CallOutcome:
    result: ModelCallResult | None = None
    failure: ModelFailure | None = None
    retry_count: int = 0
    latency_ms: int = 0
    provider_status: int | None = None


def _call_with_retries(
    client: ModelClient,
    request: ModelRequest,
    prompt: str,
    settings: ModelSettings,
) -> _CallOutcome:
    retry_count = 0
    attempts = 0
    latency_ms = 0
    while True:
        attempts += 1
        started = time.perf_counter()
        try:
            result = client.complete(request, prompt)
        except ModelClientError as exc:
            latency_ms += _elapsed(started)
            if exc.category in _RETRYABLE and attempts <= settings.max_retries:
                retry_count += 1
                continue
            if exc.category in _RETRYABLE and attempts > 1:
                failure = ModelFailure.from_category(
                    ModelFailureCategory.RETRY_EXHAUSTED,
                    f"retries exhausted after {attempts} attempt(s): {exc}",
                    attempts=attempts,
                    cause=exc.category.value,
                )
            else:
                failure = ModelFailure.from_category(exc.category, str(exc), attempts=attempts)
            return _CallOutcome(
                failure=failure,
                retry_count=retry_count,
                latency_ms=latency_ms,
                provider_status=exc.provider_status,
            )
        latency_ms += _elapsed(started)
        size_failure: ModelFailure | None = None
        text = result.text
        if len(text.encode("utf-8")) > settings.max_response_bytes:
            size_failure = ModelFailure.from_category(
                ModelFailureCategory.EXCESSIVE_RESPONSE,
                f"response is {len(text.encode('utf-8'))} bytes, exceeding "
                f"max_response_bytes={settings.max_response_bytes}",
                attempts=attempts,
            )
        if size_failure is not None:
            return _CallOutcome(
                result=result,
                failure=size_failure,
                retry_count=retry_count,
                latency_ms=latency_ms,
                provider_status=result.metadata.provider_status,
            )
        return _CallOutcome(
            result=result,
            retry_count=retry_count,
            latency_ms=latency_ms,
            provider_status=result.metadata.provider_status,
        )


def _classify(
    observations: tuple[Observation, ...], report: ReviewReport
) -> tuple[list[ClassifiedObservation], list[ClassifiedObservation]]:
    coverage = {item.rule_ref: item for item in report.coverage}
    assisted: list[ClassifiedObservation] = []
    unverified: list[ClassifiedObservation] = []
    for observation in observations:
        category = observation.category.value
        if category in _UNSUPPORTED_CATEGORIES:
            unverified.append(
                ClassifiedObservation(
                    observation=observation,
                    classification=ObservationClassification.UNVERIFIED,
                    deterministic_consistency=(
                        "no deterministic confirmation path exists for this category in "
                        "Stage 3; the observation requires manual review"
                    ),
                    note="not verified against routing behaviour",
                )
            )
            continue
        if not observation.evidence:
            unverified.append(
                ClassifiedObservation(
                    observation=observation,
                    classification=ObservationClassification.UNVERIFIED,
                    deterministic_consistency="no supplied evidence excerpt was quoted",
                    note="requires manual review",
                )
            )
            continue
        if observation.rule_reference is None:
            unverified.append(
                ClassifiedObservation(
                    observation=observation,
                    classification=ObservationClassification.UNVERIFIED,
                    deterministic_consistency=(
                        "the observation cites no effective requirement rule, so no "
                        "deterministic behaviour can be checked"
                    ),
                    note="requires manual review",
                )
            )
            continue
        item = coverage.get(observation.rule_reference)
        if item is None:
            unverified.append(
                ClassifiedObservation(
                    observation=observation,
                    classification=ObservationClassification.UNVERIFIED,
                    deterministic_consistency=(
                        f"rule {observation.rule_reference!r} has no deterministic coverage entry"
                    ),
                    note="requires manual review",
                )
            )
            continue
        if not item.covered:
            unverified.append(
                ClassifiedObservation(
                    observation=observation,
                    classification=ObservationClassification.UNVERIFIED,
                    deterministic_consistency=(
                        f"rule {observation.rule_reference!r} was not covered by any "
                        "evaluated ticket"
                    ),
                    note="requires manual review",
                )
            )
            continue
        assisted.append(
            ClassifiedObservation(
                observation=observation,
                classification=ObservationClassification.ASSISTED,
                deterministic_consistency=(
                    f"rule {observation.rule_reference!r} is deterministically covered and "
                    f"routes to {item.destination!r}; the quotation matches supplied evidence"
                ),
                note=(
                    "references and quotations are consistent with the deterministic "
                    "review; the interpretation remains advisory"
                ),
            )
        )
    return assisted, unverified


def _finding_summaries(report: ReviewReport) -> tuple[FindingSummary, ...]:
    return tuple(
        FindingSummary(
            finding_id=finding.finding_id,
            severity=finding.severity.value,
            category=finding.category.value,
            message=finding.message,
            rule_ref=finding.rule_ref,
            artifact_id=finding.artifact_id,
            artifact_version=finding.artifact_version,
            explanation=finding.explanation,
        )
        for finding in report.findings
    )


def _failed(
    settings: ModelSettings,
    report: ReviewReport,
    request: ModelRequestProvenance,
    failure: ModelFailure,
    *,
    synthetic: bool,
    response: ModelResponseProvenance,
    rejected: tuple[RejectedObservation, ...] = (),
) -> ModelAnalysis:
    return ModelAnalysis(
        status=ModelAnalysisStatus.FAILED,
        provider=settings.mode.value,
        model=settings.model,
        prompt_version=settings.prompt_version,
        synthetic=synthetic,
        deterministic_status=report.status.value,
        confirmed_findings=_finding_summaries(report),
        rejected_observations=rejected,
        request=request,
        response=response,
        failure=failure,
    )


def _terminal(
    settings: ModelSettings,
    report: ReviewReport,
    request: ModelRequestProvenance,
    failure: ModelFailure,
    *,
    synthetic: bool,
) -> ModelAnalysis:
    return ModelAnalysis(
        status=ModelAnalysisStatus.FAILED,
        provider=settings.mode.value,
        model=settings.model,
        prompt_version=settings.prompt_version,
        synthetic=synthetic,
        deterministic_status=report.status.value,
        confirmed_findings=_finding_summaries(report),
        request=request,
        failure=failure,
    )


def _skipped(
    settings: ModelSettings,
    report: ReviewReport,
    category: ModelFailureCategory,
    message: str,
    timestamp: str,
    *,
    request: ModelRequestProvenance | None = None,
) -> ModelAnalysis:
    provenance = request or _request_provenance(settings, "", timestamp)
    return ModelAnalysis(
        status=ModelAnalysisStatus.SKIPPED,
        provider=settings.mode.value,
        model=settings.model,
        prompt_version=settings.prompt_version,
        synthetic=settings.mode is ModelMode.FAKE,
        deterministic_status=report.status.value,
        confirmed_findings=_finding_summaries(report),
        request=provenance,
        failure=ModelFailure.from_category(category, message),
        skipped_reason=message,
    )


def _request_provenance(
    settings: ModelSettings, prompt: str, timestamp: str
) -> ModelRequestProvenance:
    encoded = prompt.encode("utf-8")
    return ModelRequestProvenance(
        provider=settings.mode.value,
        model=settings.model,
        prompt_version=settings.prompt_version,
        generation=_generation(settings),
        request_sha256=hashlib.sha256(encoded).hexdigest(),
        input_bytes=len(encoded),
        timestamp=timestamp,
        timeout_seconds=settings.timeout_seconds,
    )


def _generation(settings: ModelSettings) -> dict[str, str | float | int]:
    return {
        "temperature": settings.temperature,
        "max_output_tokens": settings.max_output_tokens,
        "max_response_bytes": settings.max_response_bytes,
        "max_observations": settings.max_observations,
    }


def _cache_key(request: ModelRequest, settings: ModelSettings) -> str:
    payload = {
        "cache": request.cache.model_dump(mode="json"),
        "prompt_version": request.prompt_version,
        "provider": request.model.provider,
        "model": request.model.model,
        "generation": _generation(settings),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_synthetic(client: ModelClient, result: ModelCallResult | None) -> bool:
    if result is not None:
        return result.metadata.synthetic
    return client.provider == "fake"


def _elapsed(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "build_client",
    "clear_model_cache",
    "run_model_analysis",
]
