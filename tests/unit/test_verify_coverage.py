from __future__ import annotations

from verityflow.ir.policy import Policy
from verityflow.models.common import ArtifactRef
from verityflow.models.dictionary import DataDictionary
from verityflow.models.spec import SpecArtifact
from verityflow.models.workflow import WorkflowArtifact
from verityflow.requirements.compile import compile_document
from verityflow.requirements.parser import parse_requirements
from verityflow.verify.coverage import analyze_coverage
from verityflow.verify.domain import build_ticket_space
from verityflow.verify.routing import build_spec_router
from verityflow.verify.workflow import build_workflow_router

ARTIFACT = ArtifactRef(artifact_id="REQ", version="1.0")
SPEC_ARTIFACT = ArtifactRef(artifact_id="SPEC", version="1.0")
WORKFLOW_ARTIFACT = ArtifactRef(artifact_id="WF", version="1.0")

SHADOWED_TEXT = """\
R1. Missing category -> manual review.
R2. Missing category -> normal queue.
R3. All remaining tickets -> normal queue.
"""


def _policy(dictionary: DataDictionary, text: str) -> Policy:
    document = parse_requirements(text, "requirements.txt", dictionary)
    policy, _ = compile_document(document, artifact=ARTIFACT)
    return policy


def _spec(dictionary: DataDictionary, priority_destination: str = "priority_queue"):
    payload = {
        "spec_id": "SPEC",
        "version": "1.0",
        "rules": [
            {
                "id": "S1",
                "priority": 1,
                "when": {"kind": "missing", "field": "category"},
                "destination": "manual_review",
            },
            {
                "id": "S2",
                "priority": 2,
                "when": {"kind": "eq", "field": "priority", "value": "urgent"},
                "destination": priority_destination,
            },
            {
                "id": "S3",
                "priority": 3,
                "when": {
                    "kind": "cmp",
                    "field": "wait_time",
                    "op": ">=",
                    "value": 30,
                    "unit": "minutes",
                },
                "destination": "escalation_queue",
            },
        ],
        "default": {"destination": "normal_queue"},
    }
    spec = SpecArtifact.model_validate(payload)
    return build_spec_router(spec, dictionary, SPEC_ARTIFACT).router


def _workflow(dictionary: DataDictionary):
    payload = {
        "workflow_id": "WF",
        "version": "1.0",
        "start": "start",
        "nodes": [
            {
                "id": "start",
                "version": "1.0",
                "kind": "decision",
                "branches": [
                    {
                        "branch_id": "b1",
                        "version": "1.0",
                        "condition": {"kind": "missing", "field": "category"},
                        "target": "manual",
                    },
                    {
                        "branch_id": "b2",
                        "version": "1.0",
                        "condition": {
                            "kind": "eq",
                            "field": "priority",
                            "value": "urgent",
                        },
                        "target": "priority",
                    },
                    {
                        "branch_id": "b3",
                        "version": "1.0",
                        "condition": {
                            "kind": "cmp",
                            "field": "wait_time",
                            "op": ">=",
                            "value": 30,
                            "unit": "minutes",
                        },
                        "target": "escalation",
                    },
                    {"branch_id": "b4", "version": "1.0", "target": "normal"},
                ],
            },
            {"id": "manual", "version": "1.0", "kind": "terminal", "destination": "manual_review"},
            {
                "id": "priority",
                "version": "1.0",
                "kind": "terminal",
                "destination": "priority_queue",
            },
            {
                "id": "escalation",
                "version": "1.0",
                "kind": "terminal",
                "destination": "escalation_queue",
            },
            {"id": "normal", "version": "1.0", "kind": "terminal", "destination": "normal_queue"},
        ],
    }
    artifact = WorkflowArtifact.model_validate(payload)
    return build_workflow_router(artifact, dictionary, WORKFLOW_ARTIFACT, max_steps=10_000).router


def _space(dictionary: DataDictionary, policy: Policy):
    return build_ticket_space(
        dictionary, [rule.condition for rule in policy.rules], max_cells=50_000
    )


def test_agreement_covers_every_requirement_with_evidence(
    dictionary: DataDictionary, starter_text: str
) -> None:
    policy = _policy(dictionary, starter_text)
    result = analyze_coverage(
        policy,
        _space(dictionary, policy),
        spec=_spec(dictionary),
        workflow=_workflow(dictionary),
    )

    assert result.uncovered == ()
    assert result.unreviewed == ()
    refs = [item.rule_ref for item in result.coverage]
    assert refs == ["R1", "R2", "R3", "R4"]
    for item in result.coverage:
        assert item.covered
        assert item.evidence
        assert item.spec_elements
        assert item.workflow_elements
    urgent = next(item for item in result.coverage if item.rule_ref == "R2")
    assert urgent.spec_elements == ("S2",)
    assert urgent.destination == "priority_queue"


def test_spec_misroute_leaves_that_requirement_uncovered(
    dictionary: DataDictionary, starter_text: str
) -> None:
    policy = _policy(dictionary, starter_text)
    result = analyze_coverage(
        policy,
        _space(dictionary, policy),
        spec=_spec(dictionary, priority_destination="manual_review"),
    )

    urgent = next(item for item in result.coverage if item.rule_ref == "R2")
    assert not urgent.covered
    assert urgent.spec_elements == ()
    others = [item for item in result.coverage if item.rule_ref != "R2"]
    assert all(item.covered for item in others)


def test_unreachable_requirement_is_reported_as_a_coverage_gap(
    dictionary: DataDictionary,
) -> None:
    policy = _policy(dictionary, SHADOWED_TEXT)
    result = analyze_coverage(policy, _space(dictionary, policy))

    assert result.uncovered == ("R2",)
    assert len(result.unreviewed) == 1
    scope = result.unreviewed[0]
    assert scope.kind == "coverage"
    assert scope.rule_refs == ("R2",)
    shadowed = next(item for item in result.coverage if item.rule_ref == "R2")
    assert not shadowed.covered
    assert shadowed.evidence == ()
