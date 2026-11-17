from __future__ import annotations

from verityflow.ir.policy import Policy
from verityflow.models.common import ArtifactRef
from verityflow.models.dictionary import DataDictionary
from verityflow.models.report import FindingCategory
from verityflow.models.workflow import WorkflowArtifact
from verityflow.requirements.compile import compile_document
from verityflow.requirements.parser import parse_requirements
from verityflow.verify.domain import build_ticket_space
from verityflow.verify.workflow import build_workflow_router, verify_workflow

STARTER_TEXT = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, waiting >= 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
"""

ARTIFACT = ArtifactRef(artifact_id="WF", version="1.0")
TERMINALS = {
    "manual": "manual_review",
    "priority": "priority_queue",
    "escalation": "escalation_queue",
    "normal": "normal_queue",
}


def _policy(dictionary: DataDictionary) -> Policy:
    document = parse_requirements(STARTER_TEXT, "requirements.txt", dictionary)
    policy, _ = compile_document(document, artifact=ArtifactRef(artifact_id="REQ", version="1.0"))
    return policy


def _missing() -> dict[str, object]:
    return {"kind": "missing", "field": "category"}


def _urgent() -> dict[str, object]:
    return {"kind": "eq", "field": "priority", "value": "urgent"}


def _wait(op: str, value: int) -> dict[str, object]:
    return {"kind": "cmp", "field": "wait_time", "op": op, "value": value, "unit": "minutes"}


def _workflow(
    branches: list[tuple[int, dict[str, object] | None, str]],
    *,
    terminals: dict[str, str] | None = None,
    extra_nodes: list[dict[str, object]] | None = None,
) -> WorkflowArtifact:
    active = TERMINALS if terminals is None else terminals
    nodes: list[dict[str, object]] = [
        {
            "id": "start",
            "version": "1.0",
            "kind": "decision",
            "branches": [
                {
                    "branch_id": f"b{index}",
                    "version": "1.0",
                    **({"condition": condition} if condition is not None else {}),
                    "target": target,
                }
                for index, condition, target in branches
            ],
        }
    ]
    for node_id, destination in active.items():
        nodes.append(
            {
                "id": node_id,
                "version": "1.0",
                "kind": "terminal",
                "destination": destination,
            }
        )
    if extra_nodes:
        nodes.extend(extra_nodes)
    return WorkflowArtifact.model_validate(
        {"workflow_id": "WF", "version": "1.0", "start": "start", "nodes": nodes}
    )


def _baseline() -> WorkflowArtifact:
    return _workflow(
        [
            (1, _missing(), "manual"),
            (2, _urgent(), "priority"),
            (3, _wait(">=", 30), "escalation"),
            (4, None, "normal"),
        ]
    )


def _verify(dictionary: DataDictionary, policy: Policy, workflow: WorkflowArtifact):
    program = build_workflow_router(workflow, dictionary, ARTIFACT, max_steps=10_000)
    expressions = [rule.condition for rule in policy.rules]
    for node in program.router.nodes.values():
        for branch in node.branches:
            if branch.condition is not None:
                expressions.append(branch.condition)
    space = build_ticket_space(dictionary, expressions, max_cells=50_000)
    result = verify_workflow(
        policy,
        workflow,
        dictionary,
        space,
        artifact=ARTIFACT,
        policy_ref="REQ@1.0",
        max_steps=10_000,
        max_signature_cells=4096,
    )
    return result, space


def _categories(result) -> set[FindingCategory]:
    return {finding.category for finding in result.findings}


def test_matching_workflow_agrees_everywhere(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)

    result, space = _verify(dictionary, policy, _baseline())

    assert result.record.equal
    assert result.findings == ()
    assert space.exhaustive


def test_workflow_equivalent_formulation_is_accepted(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    workflow = _workflow(
        [
            (1, _missing(), "manual"),
            (2, _urgent(), "priority"),
            (3, _wait(">", 29), "escalation"),
            (4, None, "normal"),
        ]
    )

    result, _ = _verify(dictionary, policy, workflow)

    assert result.record.equal
    assert result.findings == ()


def test_workflow_branch_order_is_precedence(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    workflow = _workflow(
        [
            (1, _missing(), "manual"),
            (2, _wait(">=", 30), "escalation"),
            (3, _urgent(), "priority"),
            (4, None, "normal"),
        ]
    )

    result, _ = _verify(dictionary, policy, workflow)

    assert _categories(result) == {FindingCategory.PRECEDENCE}
    overlapping = [
        counterexample
        for counterexample in result.record.counterexamples
        if counterexample.inputs.get("priority") == "urgent"
        and isinstance(counterexample.inputs.get("wait_time"), int)
        and counterexample.inputs["wait_time"] >= 30
    ]
    assert overlapping
    assert all(counterexample.validated for counterexample in result.record.counterexamples)


def test_workflow_strictness_change_is_a_threshold_change(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    workflow = _workflow(
        [
            (1, _missing(), "manual"),
            (2, _urgent(), "priority"),
            (3, _wait(">", 30), "escalation"),
            (4, None, "normal"),
        ]
    )

    result, _ = _verify(dictionary, policy, workflow)

    assert FindingCategory.THRESHOLD_CHANGED in _categories(result)
    assert any(
        counterexample.inputs.get("wait_time") == 30
        for counterexample in result.record.counterexamples
    )


def test_workflow_wrong_destination(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    terminals = {**TERMINALS, "priority": "escalation_queue"}
    workflow = _workflow(
        [
            (1, _missing(), "manual"),
            (2, _urgent(), "priority"),
            (3, _wait(">=", 30), "escalation"),
            (4, None, "normal"),
        ],
        terminals=terminals,
    )

    result, _ = _verify(dictionary, policy, workflow)

    assert FindingCategory.WRONG_DESTINATION in _categories(result)


def test_missing_else_branch_falls_through(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    workflow = _workflow(
        [
            (1, _missing(), "manual"),
            (2, _urgent(), "priority"),
            (3, _wait(">=", 30), "escalation"),
        ]
    )

    result, _ = _verify(dictionary, policy, workflow)

    assert FindingCategory.MISSING_BRANCH in _categories(result)
    assert FindingCategory.STRUCTURAL in _categories(result)
    assert any(
        counterexample.artifact_outcome.startswith("no_match")
        for counterexample in result.record.counterexamples
    )


def test_unreachable_terminal_is_disclosed(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    workflow = _workflow(
        [
            (1, _missing(), "manual"),
            (2, _urgent(), "priority"),
            (3, _wait(">=", 30), "escalation"),
            (4, None, "normal"),
        ],
        terminals={**TERMINALS, "orphan": "billing_queue"},
    )

    result, _ = _verify(dictionary, policy, workflow)

    assert FindingCategory.UNREACHABLE_NODE in _categories(result)
    finding = next(
        finding
        for finding in result.findings
        if finding.category is FindingCategory.UNREACHABLE_NODE
    )
    assert "orphan" in finding.message


def test_cycle_is_a_distinct_outcome(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    workflow = WorkflowArtifact.model_validate(
        {
            "workflow_id": "WF",
            "version": "1.0",
            "start": "loop",
            "nodes": [
                {
                    "id": "loop",
                    "version": "1.0",
                    "kind": "decision",
                    "branches": [
                        {
                            "branch_id": "b1",
                            "version": "1.0",
                            "condition": _missing(),
                            "target": "loop",
                        },
                        {"branch_id": "b2", "version": "1.0", "target": "loop"},
                    ],
                }
            ],
        }
    )

    result, _ = _verify(dictionary, policy, workflow)

    assert not result.record.equal
    assert any(
        counterexample.artifact_outcome.startswith("error")
        for counterexample in result.record.counterexamples
    )


def test_unknown_target_is_structural(dictionary: DataDictionary) -> None:
    policy = _policy(dictionary)
    workflow = _workflow(
        [
            (1, _missing(), "manual"),
            (2, _urgent(), "ghost"),
            (3, _wait(">=", 30), "escalation"),
            (4, None, "normal"),
        ]
    )

    result, _ = _verify(dictionary, policy, workflow)

    assert FindingCategory.STRUCTURAL in _categories(result)
    assert any(
        counterexample.artifact_outcome.startswith("unsupported")
        for counterexample in result.record.counterexamples
    )
