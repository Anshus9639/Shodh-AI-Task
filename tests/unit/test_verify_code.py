from __future__ import annotations

from fractions import Fraction

from verityflow.config import Config
from verityflow.ir.policy import Policy
from verityflow.models.common import ArtifactRef
from verityflow.models.dictionary import DataDictionary
from verityflow.models.manifest import CodeLink, FieldMapEntry
from verityflow.models.report import FindingCategory
from verityflow.models.workflow import WorkflowArtifact
from verityflow.requirements.compile import compile_document
from verityflow.requirements.parser import parse_requirements
from verityflow.verify.code import verify_code
from verityflow.verify.domain import build_ticket_space
from verityflow.verify.python_ast import CodeProgram, compile_router
from verityflow.verify.workflow import build_workflow_router

STARTER_TEXT = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, waiting >= 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
"""

CODE_ARTIFACT = ArtifactRef(artifact_id="ROUTER", version="1.0")
WORKFLOW_ARTIFACT = ArtifactRef(artifact_id="WF", version="1.0")

DESTINATIONS = {
    "manual": "manual_review",
    "priority": "priority_queue",
    "escalation": "escalation_queue",
    "normal": "normal_queue",
}

CLEAN = """\
def route(ticket):
    if "category" not in ticket:
        return "manual"
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if "wait_time" in ticket and ticket["wait_time"] >= 30:
        return "escalation"
    return "normal"
"""

REORDERED = """\
def route(ticket):
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if "category" not in ticket:
        return "manual"
    if "wait_time" in ticket and ticket["wait_time"] >= 30:
        return "escalation"
    return "normal"
"""


def _policy(dictionary: DataDictionary) -> Policy:
    document = parse_requirements(STARTER_TEXT, "requirements.txt", dictionary)
    policy, _ = compile_document(document, artifact=ArtifactRef(artifact_id="REQ", version="1.0"))
    return policy


def _program(
    dictionary: DataDictionary,
    source: str,
    *,
    field_map: dict[str, FieldMapEntry] | None = None,
) -> CodeProgram:
    return compile_router(
        source,
        link=CodeLink(
            entrypoint="route",
            param="ticket",
            destination_map=DESTINATIONS,
            field_map=field_map or {},
        ),
        dictionary=dictionary,
        artifact=CODE_ARTIFACT,
        limits=Config().limits,
        path="router.py",
    )


def _space(dictionary: DataDictionary, policy: Policy, program: CodeProgram):
    hints = program.domain_hints()
    return build_ticket_space(
        dictionary,
        [rule.condition for rule in policy.rules],
        max_cells=50_000,
        extra_cuts=hints.cuts,
        extra_literals=hints.literals,
    )


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
    return build_workflow_router(
        WorkflowArtifact.model_validate(payload), dictionary, WORKFLOW_ARTIFACT, max_steps=10_000
    ).router


def _verify(dictionary: DataDictionary, source: str, *, workflow: bool = False):
    policy = _policy(dictionary)
    program = _program(dictionary, source)
    space = _space(dictionary, policy, program)
    result = verify_code(
        policy,
        program,
        dictionary,
        space,
        policy_ref="REQ@1.0",
        max_signature_cells=4096,
        workflow=_workflow(dictionary) if workflow else None,
        workflow_ref="WF@1.0",
    )
    return result, space


def _categories(result) -> set[FindingCategory]:
    return {finding.category for finding in result.findings}


def test_matching_router_agrees_everywhere(dictionary: DataDictionary) -> None:
    result, space = _verify(dictionary, CLEAN, workflow=True)
    assert result.record.equal
    assert result.findings == ()
    assert result.counterexamples == ()
    assert space.exhaustive
    assert result.workflow_agreement is not None
    assert result.workflow_agreement.equal


def test_reordered_guards_are_reported_as_precedence(dictionary: DataDictionary) -> None:
    result, _ = _verify(dictionary, REORDERED)
    assert not result.record.equal
    assert _categories(result) == {FindingCategory.PRECEDENCE}
    assert result.counterexamples
    witness = result.counterexamples[0]
    assert witness.validated
    assert witness.inputs["category"] is None
    assert witness.inputs["priority"] == "urgent"
    assert witness.requirement_outcome == "destination 'manual_review'"
    assert witness.artifact_outcome == "destination 'priority_queue'"


def test_threshold_change_is_classified_without_rule_ids(dictionary: DataDictionary) -> None:
    source = CLEAN.replace(">= 30", ">= 20")
    result, _ = _verify(dictionary, source)
    categories = _categories(result)
    assert FindingCategory.THRESHOLD_CHANGED in categories
    witnesses = [item for item in result.counterexamples if item.validated]
    assert witnesses
    assert any(20 <= int(str(item.inputs["wait_time"])) < 30 for item in witnesses)


def test_wrong_operator_direction_is_detected(dictionary: DataDictionary) -> None:
    source = CLEAN.replace(">= 30", "< 30")
    result, _ = _verify(dictionary, source)
    assert FindingCategory.WRONG_OPERATOR in _categories(result)


def test_runtime_key_error_is_reported_separately(dictionary: DataDictionary) -> None:
    source = """\
def route(ticket):
    category = ticket["category"]
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if "wait_time" in ticket and ticket["wait_time"] >= 30:
        return "escalation"
    return "normal"
"""
    result, _ = _verify(dictionary, source)
    assert FindingCategory.RUNTIME_ERROR in _categories(result)
    runtime = [item for item in result.findings if item.category is FindingCategory.RUNTIME_ERROR]
    assert runtime[0].severity.value == "BLOCKER"
    assert "KeyError" in runtime[0].message
    assert any(
        "KeyError" in item.artifact_outcome for item in result.counterexamples if item.validated
    )


def test_falling_through_to_none_is_a_script_disagreement(
    dictionary: DataDictionary,
) -> None:
    source = CLEAN.replace('    return "normal"\n', "    return None\n")
    result, _ = _verify(dictionary, source)
    assert FindingCategory.SCRIPT_DISAGREEMENT in _categories(result)
    assert any(item.artifact_outcome.startswith("no_match") for item in result.counterexamples)


def test_undeclared_destination_is_unsupported_not_agreement(
    dictionary: DataDictionary,
) -> None:
    source = CLEAN.replace('    return "normal"\n', '    return "nowhere"\n')
    result, _ = _verify(dictionary, source)
    assert FindingCategory.UNSUPPORTED_CODE in _categories(result)
    unsupported = [
        item for item in result.findings if item.category is FindingCategory.UNSUPPORTED_CODE
    ]
    assert unsupported[0].severity.value == "COVERAGE_GAP"
    assert any(
        "undeclared destination" in item.artifact_outcome
        for item in result.counterexamples
        if item.validated
    )


def test_code_literals_extend_the_ticket_space(dictionary: DataDictionary) -> None:
    source = """\
def route(ticket):
    if "category" in ticket and ticket["category"] == "vip":
        return "manual"
    return "normal"
"""
    policy = _policy(dictionary)
    program = _program(dictionary, source)
    hints = program.domain_hints()
    assert hints.literals["category"] == ("vip",)
    space = _space(dictionary, policy, program)
    values = {cell.value("category") for cell in space.cells if cell.is_present("category")}
    assert "vip" in values


def test_code_units_are_converted_into_field_units(dictionary: DataDictionary) -> None:
    source = """\
def route(ticket):
    if "wait_seconds" in ticket and ticket["wait_seconds"] >= 1800:
        return "escalation"
    return "normal"
"""
    program = _program(
        dictionary,
        source,
        field_map={"wait_seconds": FieldMapEntry(field="wait_time", unit="seconds")},
    )
    hints = program.domain_hints()
    assert hints.cuts["wait_time"] == (Fraction(30),)


def test_code_workflow_agreement_records_disagreement(
    dictionary: DataDictionary,
) -> None:
    policy = _policy(dictionary)
    program = _program(dictionary, CLEAN.replace(">= 30", ">= 20"))
    space = _space(dictionary, policy, program)
    result = verify_code(
        policy,
        program,
        dictionary,
        space,
        policy_ref="REQ@1.0",
        max_signature_cells=4096,
        workflow=_workflow(dictionary),
        workflow_ref="WF@1.0",
    )
    agreement = result.workflow_agreement
    assert agreement is not None
    assert not agreement.equal
    assert agreement.categories == (FindingCategory.SCRIPT_DISAGREEMENT.value,)
    assert agreement.counterexamples
    assert agreement.counterexamples[0].comparison == "code_vs_workflow"
