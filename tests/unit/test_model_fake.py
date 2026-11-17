from __future__ import annotations

import json
from pathlib import Path

import pytest

from verityflow.config import ModelSettings
from verityflow.ingest.loader import load_bundle
from verityflow.model.client import ModelClientError
from verityflow.model.fake import SCENARIOS, FakeModelClient
from verityflow.model.models import ModelFailureCategory, ModelMode
from verityflow.model.request import build_model_request
from verityflow.verify.review import review_loaded

ROOT = Path(__file__).resolve().parent.parent.parent
DEV = ROOT / "fixtures" / "dev"


def _request():
    loaded = load_bundle(DEV / "B10_router_clean")
    report = review_loaded(loaded)
    return build_model_request(loaded, report, ModelSettings(mode=ModelMode.FAKE))


@pytest.mark.parametrize(
    "scenario",
    sorted(SCENARIOS - {"timeout", "rate_limit", "retry_exhaustion"}),
)
def test_scenarios_are_reproducible_and_synthetic(scenario: str) -> None:
    request = _request()
    first = FakeModelClient(scenario).complete(request, "prompt")
    second = FakeModelClient(scenario).complete(request, "prompt")
    assert first.text == second.text
    assert first.metadata.synthetic
    assert first.metadata.usage is not None
    assert first.metadata.usage.synthetic
    assert first.metadata.usage.input_tokens is None
    assert first.metadata.usage.output_tokens is None


@pytest.mark.parametrize(
    ("scenario", "category"),
    [
        ("timeout", ModelFailureCategory.TIMEOUT),
        ("rate_limit", ModelFailureCategory.RATE_LIMIT),
        ("retry_exhaustion", ModelFailureCategory.TIMEOUT),
    ],
)
def test_failure_scenarios_raise_classified_errors(
    scenario: str, category: ModelFailureCategory
) -> None:
    client = FakeModelClient(scenario)
    with pytest.raises(ModelClientError) as excinfo:
        client.complete(_request(), "prompt")
    assert excinfo.value.category is category
    assert client.call_count == 1


def test_valid_scenario_is_structured_json() -> None:
    result = FakeModelClient("valid").complete(_request(), "prompt")
    payload = json.loads(result.text)
    assert set(payload) == {"analysis_summary", "observations", "ambiguities", "suggested_tests"}
    assert payload["observations"]


def test_unknown_scenario_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown fake model scenario"):
        FakeModelClient("not-a-scenario")
