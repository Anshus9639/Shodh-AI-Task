from __future__ import annotations

import json
from pathlib import Path

from verityflow.config import ModelSettings
from verityflow.ingest.loader import load_bundle
from verityflow.model.models import ModelFailureCategory, ModelMode
from verityflow.model.request import build_model_request
from verityflow.model.validate import validate_response
from verityflow.verify.review import review_loaded

ROOT = Path(__file__).resolve().parent.parent.parent
DEV = ROOT / "fixtures" / "dev"


def _request():
    loaded = load_bundle(DEV / "B10_router_clean")
    report = review_loaded(loaded)
    return build_model_request(loaded, report, ModelSettings(mode=ModelMode.FAKE))


def _validate(text: str, *, max_response_bytes: int = 131_072, max_observations: int = 32):
    return validate_response(
        text,
        _request(),
        max_response_bytes=max_response_bytes,
        max_observations=max_observations,
    )


def _response(**updates: object) -> str:
    payload: dict[str, object] = {
        "analysis_summary": "Synthetic summary.",
        "observations": [],
        "ambiguities": [],
        "suggested_tests": [],
    }
    payload.update(updates)
    return json.dumps(payload)


def _observation(**updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "observation_id": "M01",
        "category": "evidence_for_review",
        "explanation": "A synthetic observation.",
        "evidence": [],
    }
    payload.update(updates)
    return payload


def test_a_minimal_response_is_accepted() -> None:
    outcome = _validate(_response())
    assert outcome.response is not None
    assert outcome.rejected == ()
    assert outcome.failure is None


def test_malformed_json_is_rejected() -> None:
    outcome = _validate("definitely not json")
    assert outcome.response is None
    assert outcome.failure is not None
    assert outcome.failure.category is ModelFailureCategory.MALFORMED_JSON


def test_truncated_json_is_distinguished() -> None:
    outcome = _validate('{"analysis_summary": "cut off", "observations": [')
    assert outcome.failure is not None
    assert outcome.failure.category is ModelFailureCategory.TRUNCATED_JSON


def test_empty_response_is_malformed() -> None:
    outcome = _validate("   ")
    assert outcome.failure is not None
    assert outcome.failure.category is ModelFailureCategory.MALFORMED_JSON


def test_unsupported_severity_field_is_a_schema_failure() -> None:
    observation = _observation(severity="BLOCKER")
    outcome = _validate(_response(observations=[observation]))
    assert outcome.response is None
    assert outcome.failure is not None
    assert outcome.failure.category is ModelFailureCategory.SCHEMA_VALIDATION


def test_invented_rule_reference_is_rejected() -> None:
    outcome = _validate(_response(observations=[_observation(rule_reference="R-999")]))
    assert outcome.response is not None
    assert outcome.response.observations == ()
    assert outcome.rejected[0].reason is ModelFailureCategory.INVENTED_REFERENCE


def test_invented_artifact_and_version_are_rejected() -> None:
    request = _request()
    known = request.available_artifacts[0]
    wrong_version = _observation(
        observation_id="M01", artifact_id=known.artifact_id, artifact_version="999.0"
    )
    unknown_artifact = _observation(
        observation_id="M02", artifact_id="ghost", artifact_version="1.0"
    )
    outcome = _validate(_response(observations=[wrong_version, unknown_artifact]))
    assert outcome.response is not None
    assert {item.reason for item in outcome.rejected} == {ModelFailureCategory.INVENTED_REFERENCE}
    assert len(outcome.rejected) == 2


def test_evidence_must_match_a_supplied_excerpt_exactly() -> None:
    request = _request()
    source = request.evidence[0]
    valid = _observation(
        evidence=[{"anchor": source.anchor, "excerpt": source.excerpt}],
        location=source.anchor,
    )
    accepted = _validate(_response(observations=[valid]))
    assert accepted.response is not None
    assert len(accepted.response.observations) == 1

    altered = _observation(
        evidence=[{"anchor": source.anchor, "excerpt": source.excerpt + " amended"}],
        location=source.anchor,
    )
    rejected = _validate(_response(observations=[altered]))
    assert rejected.response is not None
    assert rejected.response.observations == ()
    assert rejected.rejected[0].reason is ModelFailureCategory.EVIDENCE_VALIDATION


def test_unknown_anchor_is_an_evidence_failure() -> None:
    observation = _observation(location="requirements.txt#L9999")
    outcome = _validate(_response(observations=[observation]))
    assert outcome.rejected[0].reason is ModelFailureCategory.EVIDENCE_VALIDATION


def test_duplicate_observation_ids_keep_only_the_first() -> None:
    outcome = _validate(_response(observations=[_observation(), _observation()]))
    assert outcome.response is not None
    assert len(outcome.response.observations) == 1
    assert outcome.rejected[0].detail.startswith("duplicate observation id")


def test_observation_count_is_bounded() -> None:
    observations = [
        _observation(observation_id="M01"),
        _observation(observation_id="M02"),
    ]
    outcome = _validate(_response(observations=observations), max_observations=1)
    assert outcome.response is None
    assert outcome.failure is not None
    assert outcome.failure.category is ModelFailureCategory.EXCESSIVE_RESPONSE


def test_response_size_is_bounded() -> None:
    outcome = _validate(_response(analysis_summary="x" * 400), max_response_bytes=200)
    assert outcome.failure is not None
    assert outcome.failure.category is ModelFailureCategory.EXCESSIVE_RESPONSE


def test_ambiguity_cannot_be_resolved_by_the_model() -> None:
    ambiguity = {
        "ambiguity_id": "A01",
        "explanation": "Looks resolved to me.",
        "requires_human_review": False,
    }
    outcome = _validate(_response(ambiguities=[ambiguity]))
    assert outcome.response is not None
    assert outcome.response.ambiguities == ()
    assert outcome.rejected[0].reason is ModelFailureCategory.SCHEMA_VALIDATION


def test_suggested_test_cannot_invent_a_field() -> None:
    test = {"test_id": "T01", "description": "Try it.", "inputs": {"ghost_field": 1}}
    outcome = _validate(_response(suggested_tests=[test]))
    assert outcome.response is not None
    assert outcome.response.suggested_tests == ()
    assert outcome.rejected[0].reason is ModelFailureCategory.INVENTED_REFERENCE
