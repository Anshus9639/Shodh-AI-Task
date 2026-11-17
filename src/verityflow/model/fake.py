"""Deterministic synthetic model client.

Every scenario is labelled synthetic: token counts are not invented, latency is
zero and the provider identity is ``fake``. Synthetic results say nothing about
the accuracy of any real model.
"""

from __future__ import annotations

import json

from verityflow.model.client import ModelCallResult, ModelClientError
from verityflow.model.models import (
    EvidenceSource,
    ModelCallMetadata,
    ModelFailureCategory,
    ModelRequest,
    ModelUsage,
    RuleSummary,
)

SCENARIOS = frozenset(
    {
        "valid",
        "candidate_mapping",
        "ambiguity",
        "fabricated_rule",
        "fabricated_artifact",
        "fabricated_evidence",
        "wrong_version",
        "malformed",
        "truncated",
        "empty",
        "conflicting",
        "blocker_claim",
        "injection",
        "timeout",
        "rate_limit",
        "excessive_output",
        "retry_exhaustion",
    }
)

_FAILURE_SCENARIOS = {
    "timeout": (ModelFailureCategory.TIMEOUT, "synthetic timeout"),
    "rate_limit": (ModelFailureCategory.RATE_LIMIT, "synthetic rate limit"),
    "retry_exhaustion": (ModelFailureCategory.TIMEOUT, "synthetic timeout"),
}

_EXCESSIVE_FILLER = 200_000


class FakeModelClient:
    provider = "fake"

    def __init__(self, scenario: str = "valid") -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown fake model scenario {scenario!r}")
        self.scenario = scenario
        self.call_count = 0
        self.last_prompt: str | None = None

    def complete(self, request: ModelRequest, prompt: str) -> ModelCallResult:
        self.call_count += 1
        self.last_prompt = prompt
        failure = _FAILURE_SCENARIOS.get(self.scenario)
        if failure is not None:
            raise ModelClientError(failure[0], failure[1])
        text = _render(self.scenario, request)
        return ModelCallResult(text=text, metadata=self._metadata(request))

    def _metadata(self, request: ModelRequest) -> ModelCallMetadata:
        return ModelCallMetadata(
            provider=self.provider,
            model=request.model.model,
            provider_request_id=f"synthetic-{self.call_count:04d}",
            model_version="fake-replay-v1",
            finish_reason="SYNTHETIC",
            usage=ModelUsage(synthetic=True),
            latency_ms=0,
            synthetic=True,
        )


def _render(scenario: str, request: ModelRequest) -> str:
    handlers = {
        "valid": _valid,
        "candidate_mapping": _candidate_mapping,
        "ambiguity": _ambiguity,
        "fabricated_rule": _fabricated_rule,
        "fabricated_artifact": _fabricated_artifact,
        "fabricated_evidence": _fabricated_evidence,
        "wrong_version": _wrong_version,
        "malformed": _malformed,
        "truncated": _truncated,
        "empty": _empty,
        "conflicting": _conflicting,
        "blocker_claim": _blocker_claim,
        "injection": _injection,
        "excessive_output": _excessive_output,
    }
    return handlers[scenario](request)


def _valid(request: ModelRequest) -> str:
    rule = _first_rule(request)
    evidence = _evidence(request, rule)
    observations = []
    if evidence is not None:
        observations.append(
            _observation(
                "M01",
                "evidence_for_review",
                rule=rule,
                evidence=[evidence],
                explanation=(
                    "The quoted line is the supplied evidence for this requirement; it is "
                    "recorded for human review and asserts no new verification."
                ),
            )
        )
    return _response(
        "Synthetic review of the supplied deterministic report; no additional behaviour "
        "was verified.",
        observations,
    )


def _candidate_mapping(request: ModelRequest) -> str:
    rule = _first_rule(request)
    evidence = _evidence(request, rule)
    return _response(
        "One possible paraphrase mapping was identified; it is unverified.",
        [
            _observation(
                "M01",
                "candidate_mapping",
                rule=rule,
                evidence=[] if evidence is None else [evidence],
                explanation=(
                    "The requirement wording may paraphrase the cited artifact condition. "
                    "This candidate has not been confirmed by the deterministic engine."
                ),
            )
        ],
    )


def _ambiguity(request: ModelRequest) -> str:
    rule = _first_rule(request)
    evidence = _evidence(request, rule)
    return _response(
        "One requirement phrase is ambiguous and needs a human decision.",
        [
            _observation(
                "M01",
                "ambiguous_requirement",
                rule=rule,
                evidence=[] if evidence is None else [evidence],
                explanation="The phrase can be read in more than one way.",
            )
        ],
        ambiguities=[
            {
                "ambiguity_id": "A01",
                "rule_reference": None if rule is None else rule.rule_ref,
                "explanation": (
                    "The wording does not state whether the boundary value satisfies the "
                    "condition; a human must resolve it."
                ),
                "requires_human_review": True,
            }
        ],
    )


def _fabricated_rule(request: ModelRequest) -> str:
    return _response(
        "A rule that does not exist in the supplied policy was cited.",
        [
            _observation(
                "M01",
                "rule_interaction",
                rule=None,
                evidence=[],
                explanation="Requirement R-999 appears to interact with the escalation rule.",
                rule_reference="R-999",
            )
        ],
    )


def _fabricated_artifact(request: ModelRequest) -> str:
    return _response(
        "An artifact that does not exist in the bundle was cited.",
        [
            _observation(
                "M01",
                "finding_explanation",
                artifact_id="ghost-artifact",
                artifact_version="1.0",
                explanation="The ghost-artifact implementation omits a branch.",
            )
        ],
    )


def _fabricated_evidence(request: ModelRequest) -> str:
    rule = _first_rule(request)
    source = request.evidence[0] if request.evidence else None
    quote = None
    if source is not None:
        quote = {
            "anchor": source.anchor,
            "excerpt": f"{source.excerpt} (amended by the model)",
        }
    return _response(
        "A quotation was altered.",
        [
            _observation(
                "M01",
                "evidence_for_review",
                rule=rule,
                evidence=[] if quote is None else [quote],
                explanation="The quoted text supports the interpretation.",
            )
        ],
    )


def _wrong_version(request: ModelRequest) -> str:
    artifact = request.available_artifacts[0] if request.available_artifacts else None
    return _response(
        "A real artifact was cited at the wrong version.",
        [
            _observation(
                "M01",
                "finding_explanation",
                artifact_id=None if artifact is None else artifact.artifact_id,
                artifact_version="999.0",
                explanation="Behaviour at version 999.0 differs from the reviewed version.",
            )
        ],
    )


def _malformed(request: ModelRequest) -> str:
    return "This is not a JSON object and cannot be validated."


def _truncated(request: ModelRequest) -> str:
    full = _valid(request)
    return full[: max(1, len(full) // 2)]


def _empty(request: ModelRequest) -> str:
    return ""


def _conflicting(request: ModelRequest) -> str:
    rule = _first_rule(request)
    return _response(
        "The developer approved the 25-minute threshold; the review should be PASS.",
        [
            _observation(
                "M01",
                "finding_explanation",
                rule=rule,
                evidence=[],
                explanation=(
                    "An approval was granted outside the bundle, so the threshold is "
                    "approved and the review can pass without further checks."
                ),
            )
        ],
    )


def _blocker_claim(request: ModelRequest) -> str:
    rule = _first_rule(request)
    evidence = _evidence(request, rule)
    return _response(
        "A blocker is claimed and the review is said to require changes.",
        [
            _observation(
                "M01",
                "finding_explanation",
                rule=rule,
                evidence=[] if evidence is None else [evidence],
                explanation=(
                    "This is a BLOCKER: the implementation is said to be wrong and the "
                    "review must fail with changes required."
                ),
            )
        ],
    )


def _injection(request: ModelRequest) -> str:
    source = _injection_source(request)
    return _response(
        "The artifact payload contains instructions; they are untrusted data and were "
        "not followed.",
        [
            _observation(
                "M01",
                "evidence_for_review",
                rule=_first_rule(request),
                evidence=[] if source is None else [source],
                explanation=(
                    "The quoted text tries to alter review behaviour, grant approval or "
                    "hide findings. It is reported as untrusted content; nothing was "
                    "approved or changed."
                ),
            )
        ],
    )


def _excessive_output(request: ModelRequest) -> str:
    return json.dumps(
        {
            "analysis_summary": "x" * _EXCESSIVE_FILLER,
            "observations": [],
            "ambiguities": [],
            "suggested_tests": [],
        }
    )


def _response(
    summary: str,
    observations: list[dict[str, object]],
    *,
    ambiguities: list[dict[str, object]] | None = None,
    suggested_tests: list[dict[str, object]] | None = None,
) -> str:
    return json.dumps(
        {
            "analysis_summary": summary,
            "observations": observations,
            "ambiguities": [] if ambiguities is None else ambiguities,
            "suggested_tests": [] if suggested_tests is None else suggested_tests,
        },
        ensure_ascii=False,
    )


def _observation(
    observation_id: str,
    category: str,
    *,
    rule: RuleSummary | None = None,
    evidence: list[dict[str, str]] | None = None,
    explanation: str,
    rule_reference: str | None = None,
    artifact_id: str | None = None,
    artifact_version: str | None = None,
) -> dict[str, object]:
    quotes = [] if evidence is None else evidence
    payload: dict[str, object] = {
        "observation_id": observation_id,
        "category": category,
        "evidence": quotes,
        "explanation": explanation,
    }
    reference = rule_reference if rule_reference is not None else _rule_ref(rule)
    if reference is not None:
        payload["rule_reference"] = reference
    if artifact_id is not None:
        payload["artifact_id"] = artifact_id
    if artifact_version is not None:
        payload["artifact_version"] = artifact_version
    if len(quotes) == 1:
        payload["location"] = quotes[0]["anchor"]
    return payload


def _rule_ref(rule: RuleSummary | None) -> str | None:
    return None if rule is None else rule.rule_ref


def _first_rule(request: ModelRequest) -> RuleSummary | None:
    return request.rules[0] if request.rules else None


def _evidence(request: ModelRequest, rule: RuleSummary | None) -> dict[str, str] | None:
    for source in request.evidence:
        if rule is None or rule.rule_ref in source.excerpt:
            return _quote(source)
    if request.evidence:
        return _quote(request.evidence[0])
    return None


def _injection_source(request: ModelRequest) -> dict[str, str] | None:
    for source in request.evidence:
        if "ignore all previous instructions" in source.excerpt.casefold():
            return _quote(source)
    return _quote(request.evidence[0]) if request.evidence else None


def _quote(source: EvidenceSource) -> dict[str, str]:
    return {"anchor": source.anchor, "excerpt": source.excerpt}


__all__ = ["SCENARIOS", "FakeModelClient"]
