from __future__ import annotations

from verityflow.ir.policy import Policy
from verityflow.models.common import ArtifactRef
from verityflow.models.dictionary import DataDictionary
from verityflow.models.report import FindingCategory
from verityflow.models.spec import SpecArtifact
from verityflow.requirements.compile import compile_document
from verityflow.requirements.parser import parse_requirements
from verityflow.verify.domain import build_ticket_space
from verityflow.verify.routing import build_spec_router
from verityflow.verify.spec import verify_spec

STARTER_TEXT = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, waiting >= 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
"""

ARTIFACT = ArtifactRef(artifact_id="SPEC", version="1.0")


def _policy(dictionary: DataDictionary) -> Policy:
    document = parse_requirements(STARTER_TEXT, "requirements.txt", dictionary)
    policy, findings = compile_document(
        document, artifact=ArtifactRef(artifact_id="REQ", version="1.0")
    )
    assert not [finding for finding in findings if finding.severity.value == "BLOCKER"]
    return policy


def _rule(
    rule_id: str,
    priority: int,
    when: dict[str, object],
    destination: str,
) -> dict[str, object]:
    return {
        "id": rule_id,
        "priority": priority,
        "when": when,
        "destination": destination,
    }


def _missing() -> dict[str, object]:
    return {"kind": "missing", "field": "category"}


def _urgent() -> dict[str, object]:
    return {"kind": "eq", "field": "priority", "value": "urgent"}


def _wait(op: str, value: int) -> dict[str, object]:
    return {"kind": "cmp", "field": "wait_time", "op": op, "value": value, "unit": "minutes"}


def _spec(rules: list[dict[str, object]], default: str | None = "normal_queue") -> SpecArtifact:
    payload: dict[str, object] = {
        "spec_id": "SPEC",
        "version": "1.0",
        "rules": rules,
    }
    if default is not None:
        payload["default"] = {"destination": default}
    return SpecArtifact.model_validate(payload)


def _verify(dictionary: DataDictionary, policy: Policy, spec: SpecArtifact):
    program = build_spec_router(spec, dictionary, ARTIFACT)
    expressions = [rule.condition for rule in policy.rules]
    expressions.extend(entry.condition for entry in program.router.entries)
    space = build_ticket_space(dictionary, expressions, max_cells=50_000)
    result = verify_spec(
        policy,
        spec,
        dictionary,
        space,
        artifact=ARTIFACT,
        policy_ref="REQ@1.0",
        max_signature_cells=4096,
    )
    return result, space


def _categories(result) -> set[FindingCategory]:
    return {finding.category for finding in result.findings}


def test_matching_spec_agrees_everywhere(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    spec = _spec(
        [
            _rule("S1", 1, _missing(), "manual_review"),
            _rule("S2", 2, _urgent(), "priority_queue"),
            _rule("S3", 3, _wait(">=", 30), "escalation_queue"),
        ]
    )

    result, space = _verify(dictionary, policy, spec)

    assert result.record.equal
    assert result.findings == ()
    assert space.exhaustive
    assert len(result.record.counterexamples) == 0


def test_renamed_ids_and_priorities_are_not_evidence(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    spec = _spec(
        [
            _rule("Q7", 10, _missing(), "manual_review"),
            _rule("Q2", 20, _urgent(), "priority_queue"),
            _rule("Q9", 30, _wait(">=", 30), "escalation_queue"),
        ]
    )

    result, _ = _verify(dictionary, policy, spec)

    assert result.record.equal
    assert result.findings == ()


def test_equivalent_integer_formulation_is_accepted(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    spec = _spec(
        [
            _rule("S1", 1, _missing(), "manual_review"),
            _rule("S2", 2, _urgent(), "priority_queue"),
            _rule("S3", 3, _wait(">", 29), "escalation_queue"),
        ]
    )

    result, space = _verify(dictionary, policy, spec)

    assert result.record.equal
    assert result.findings == ()
    assert any(cell.value("wait_time") == 29 for cell in space.cells)


def test_strictness_change_is_a_threshold_change(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    spec = _spec(
        [
            _rule("S1", 1, _missing(), "manual_review"),
            _rule("S2", 2, _urgent(), "priority_queue"),
            _rule("S3", 3, _wait(">", 30), "escalation_queue"),
        ]
    )

    result, _ = _verify(dictionary, policy, spec)

    assert FindingCategory.THRESHOLD_CHANGED in _categories(result)
    assert any(
        counterexample.inputs.get("wait_time") == 30
        for counterexample in result.record.counterexamples
    )
    assert all(counterexample.validated for counterexample in result.record.counterexamples)


def test_direction_change_is_a_wrong_operator(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    spec = _spec(
        [
            _rule("S1", 1, _missing(), "manual_review"),
            _rule("S2", 2, _urgent(), "priority_queue"),
            _rule("S3", 3, _wait("<", 30), "escalation_queue"),
        ]
    )

    result, _ = _verify(dictionary, policy, spec)

    assert FindingCategory.WRONG_OPERATOR in _categories(result)


def test_reordering_plausible_rules_is_precedence(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    spec = _spec(
        [
            _rule("S1", 1, _missing(), "manual_review"),
            _rule("S3", 2, _wait(">=", 30), "escalation_queue"),
            _rule("S2", 3, _urgent(), "priority_queue"),
        ]
    )

    result, _ = _verify(dictionary, policy, spec)

    assert _categories(result) == {FindingCategory.PRECEDENCE}
    overlapping = [
        counterexample
        for counterexample in result.record.counterexamples
        if counterexample.inputs.get("priority") == "urgent"
        and isinstance(counterexample.inputs.get("wait_time"), int)
        and counterexample.inputs["wait_time"] >= 30
    ]
    assert overlapping
    assert all(counterexample.validated for counterexample in overlapping)


def test_missing_rule_is_detected_without_ids(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    spec = _spec(
        [
            _rule("S1", 1, _missing(), "manual_review"),
            _rule("S3", 2, _wait(">=", 30), "escalation_queue"),
        ]
    )

    result, _ = _verify(dictionary, policy, spec)

    categories = _categories(result)
    assert FindingCategory.MISSING_REQUIREMENT in categories
    assert FindingCategory.MISSING_BRANCH in categories
    missing = [
        finding
        for finding in result.findings
        if finding.category is FindingCategory.MISSING_REQUIREMENT
    ]
    assert missing[0].rule_ref == "R2"


def test_equivalent_condition_with_other_destination(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    spec = _spec(
        [
            _rule("S1", 1, _missing(), "manual_review"),
            _rule("S2", 2, _urgent(), "escalation_queue"),
            _rule("S3", 3, _wait(">=", 30), "escalation_queue"),
        ]
    )

    result, _ = _verify(dictionary, policy, spec)

    assert FindingCategory.WRONG_DESTINATION in _categories(result)


def test_spec_without_default_discloses_missing_branch(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    spec = _spec(
        [
            _rule("S1", 1, _missing(), "manual_review"),
            _rule("S2", 2, _urgent(), "priority_queue"),
            _rule("S3", 3, _wait(">=", 30), "escalation_queue"),
        ],
        default=None,
    )

    result, _ = _verify(dictionary, policy, spec)

    assert FindingCategory.MISSING_BRANCH in _categories(result)
    assert any(
        counterexample.artifact_outcome.startswith("no_match")
        for counterexample in result.record.counterexamples
    )


def test_duplicate_priorities_are_unreviewed(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    spec = _spec(
        [
            _rule("S1", 1, _missing(), "manual_review"),
            _rule("S2", 1, _urgent(), "priority_queue"),
            _rule("S3", 2, _wait(">=", 30), "escalation_queue"),
        ]
    )

    result, _ = _verify(dictionary, policy, spec)

    assert not result.record.equal
    assert result.record.mode.value == "unreviewed"
    assert FindingCategory.AMBIGUITY in _categories(result)
