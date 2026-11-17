"""Strict validation of model responses against the supplied request.

A response is untrusted input. It must be complete JSON, match the frozen
response schema, cite only supplied rule references, artifact identities and
evidence anchors, and quote evidence exactly. Invalid observations are rejected
individually and recorded; malformed, truncated, oversize or schema-invalid
responses fail as a whole. Nothing here can create or change a deterministic
finding.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from verityflow.model.models import (
    Ambiguity,
    ModelFailure,
    ModelFailureCategory,
    ModelRequest,
    ModelResponse,
    Observation,
    RejectedObservation,
    SuggestedTest,
)


@dataclass(frozen=True, slots=True)
class ResponseValidation:
    response: ModelResponse | None
    rejected: tuple[RejectedObservation, ...] = ()
    failure: ModelFailure | None = None


def validate_response(
    text: str,
    request: ModelRequest,
    *,
    max_response_bytes: int,
    max_observations: int,
) -> ResponseValidation:
    if len(text.encode("utf-8")) > max_response_bytes:
        return _fatal(
            ModelFailureCategory.EXCESSIVE_RESPONSE,
            f"response is {len(text.encode('utf-8'))} bytes, exceeding "
            f"max_response_bytes={max_response_bytes}",
        )
    if not text.strip():
        return _fatal(ModelFailureCategory.MALFORMED_JSON, "response is empty")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        category = (
            ModelFailureCategory.TRUNCATED_JSON
            if _looks_truncated(text)
            else ModelFailureCategory.MALFORMED_JSON
        )
        return _fatal(category, f"response is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        return _fatal(
            ModelFailureCategory.SCHEMA_VALIDATION,
            "response must be a JSON object",
        )
    try:
        response = ModelResponse.model_validate(payload)
    except ValidationError as exc:
        return _fatal(
            ModelFailureCategory.SCHEMA_VALIDATION,
            f"response does not match the model response schema: {_short(exc)}",
        )
    if len(response.observations) > max_observations:
        return _fatal(
            ModelFailureCategory.EXCESSIVE_RESPONSE,
            f"response has {len(response.observations)} observations, exceeding "
            f"max_observations={max_observations}",
        )

    catalogue = _Catalogue(request)
    rejected: list[RejectedObservation] = []
    observations = _unique(
        response.observations,
        lambda item: item.observation_id,
        "observation id",
        rejected,
        category="observation",
    )
    ambiguities = _unique(
        response.ambiguities,
        lambda item: item.ambiguity_id,
        "ambiguity id",
        rejected,
        category="ambiguity",
    )
    tests = _unique(
        response.suggested_tests,
        lambda item: item.test_id,
        "test id",
        rejected,
        category="suggested_test",
    )

    accepted_observations: list[Observation] = []
    for observation in observations:
        failure, detail = catalogue.check(observation)
        if failure is None:
            accepted_observations.append(observation)
        else:
            rejected.append(
                RejectedObservation(
                    observation_id=observation.observation_id,
                    category=observation.category.value,
                    reason=failure,
                    detail=detail,
                )
            )
    accepted_ambiguities: list[Ambiguity] = []
    for ambiguity in ambiguities:
        if not ambiguity.requires_human_review:
            rejected.append(
                RejectedObservation(
                    observation_id=ambiguity.ambiguity_id,
                    category="ambiguity",
                    reason=ModelFailureCategory.SCHEMA_VALIDATION,
                    detail="an ambiguity cannot be resolved by the model; human review is required",
                )
            )
            continue
        if ambiguity.rule_reference is not None and not catalogue.has_rule(
            ambiguity.rule_reference
        ):
            rejected.append(
                RejectedObservation(
                    observation_id=ambiguity.ambiguity_id,
                    category="ambiguity",
                    reason=ModelFailureCategory.INVENTED_REFERENCE,
                    detail=f"rule reference {ambiguity.rule_reference!r} was not supplied",
                )
            )
            continue
        accepted_ambiguities.append(ambiguity)
    accepted_tests: list[SuggestedTest] = []
    for test in tests:
        unknown = sorted(set(test.inputs) - set(request.available_fields))
        if unknown:
            rejected.append(
                RejectedObservation(
                    observation_id=test.test_id,
                    category="suggested_test",
                    reason=ModelFailureCategory.INVENTED_REFERENCE,
                    detail=f"unknown ticket fields: {', '.join(unknown)}",
                )
            )
            continue
        accepted_tests.append(test)

    validated = ModelResponse(
        analysis_summary=response.analysis_summary,
        observations=tuple(accepted_observations),
        ambiguities=tuple(accepted_ambiguities),
        suggested_tests=tuple(accepted_tests),
    )
    return ResponseValidation(response=validated, rejected=tuple(rejected))


def _fatal(category: ModelFailureCategory, message: str) -> ResponseValidation:
    return ResponseValidation(
        response=None,
        failure=ModelFailure.from_category(category, message),
    )


def _short(exc: Exception) -> str:
    text = str(exc)
    return text if len(text) <= 600 else text[:600] + "..."


def _looks_truncated(text: str) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "{[":
            depth += 1
        elif character in "}]":
            depth -= 1
    return in_string or depth > 0


def _unique[T](
    items: tuple[T, ...],
    key: Callable[[T], str],
    label: str,
    rejected: list[RejectedObservation],
    *,
    category: str,
) -> list[T]:
    seen: set[str] = set()
    kept: list[T] = []
    for item in items:
        identifier = key(item)
        if identifier in seen:
            rejected.append(
                RejectedObservation(
                    observation_id=identifier,
                    category=category,
                    reason=ModelFailureCategory.SCHEMA_VALIDATION,
                    detail=f"duplicate {label} {identifier!r}",
                )
            )
            continue
        seen.add(identifier)
        kept.append(item)
    return kept


class _Catalogue:
    def __init__(self, request: ModelRequest) -> None:
        self._rules = set(request.available_rule_refs)
        self._artifacts = {
            (artifact.artifact_id, artifact.version) for artifact in request.available_artifacts
        }
        self._ids = {artifact.artifact_id for artifact in request.available_artifacts}
        self._versions = {artifact.version for artifact in request.available_artifacts}
        self._evidence = {
            (source.artifact_id, source.artifact_version, source.anchor, source.excerpt)
            for source in request.evidence
        }
        self._anchors = {(source.artifact_id, source.anchor) for source in request.evidence}
        self._anchor_names = {source.anchor for source in request.evidence}

    def has_rule(self, reference: str) -> bool:
        return reference in self._rules

    def check(self, observation: Observation) -> tuple[ModelFailureCategory | None, str]:
        if observation.rule_reference is not None and not self.has_rule(observation.rule_reference):
            return (
                ModelFailureCategory.INVENTED_REFERENCE,
                f"rule reference {observation.rule_reference!r} was not supplied",
            )
        if observation.artifact_id is not None and observation.artifact_id not in self._ids:
            return (
                ModelFailureCategory.INVENTED_REFERENCE,
                f"artifact id {observation.artifact_id!r} was not supplied",
            )
        if observation.artifact_version is not None:
            if observation.artifact_id is not None:
                pair = (observation.artifact_id, observation.artifact_version)
                if pair not in self._artifacts:
                    return (
                        ModelFailureCategory.INVENTED_REFERENCE,
                        f"artifact {observation.artifact_id!r} has no supplied version "
                        f"{observation.artifact_version!r}",
                    )
            elif observation.artifact_version not in self._versions:
                return (
                    ModelFailureCategory.INVENTED_REFERENCE,
                    f"artifact version {observation.artifact_version!r} was not supplied",
                )
        if observation.location is not None and observation.artifact_id is not None:
            if (observation.artifact_id, observation.location) not in self._anchors:
                return (
                    ModelFailureCategory.EVIDENCE_VALIDATION,
                    f"anchor {observation.location!r} does not belong to artifact "
                    f"{observation.artifact_id!r}",
                )
        if observation.location is not None and observation.artifact_id is None:
            if observation.location not in self._anchor_names:
                return (
                    ModelFailureCategory.EVIDENCE_VALIDATION,
                    f"anchor {observation.location!r} was not supplied",
                )
        for quote in observation.evidence:
            provenances = [
                item
                for item in self._evidence
                if item[2] == quote.anchor and item[3] == quote.excerpt
            ]
            if not provenances:
                return (
                    ModelFailureCategory.EVIDENCE_VALIDATION,
                    f"quote at {quote.anchor!r} does not match any supplied evidence excerpt",
                )
            if observation.artifact_id is not None and all(
                item[0] != observation.artifact_id for item in provenances
            ):
                return (
                    ModelFailureCategory.EVIDENCE_VALIDATION,
                    f"quote at {quote.anchor!r} does not belong to artifact "
                    f"{observation.artifact_id!r}",
                )
        return None, ""


__all__ = ["ResponseValidation", "validate_response"]
