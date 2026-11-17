from __future__ import annotations

from pathlib import Path

from verityflow.config import Config, Limits
from verityflow.ingest.artifacts import TypedArtifacts, parse_artifacts
from verityflow.ingest.loader import load_bundle
from verityflow.ir.model import And, Const, Eq, NotTrue
from verityflow.mapping.extract import (
    extract_requirements,
    extract_spec,
    extract_workflow,
)
from verityflow.mapping.matcher import map_to_elements
from verityflow.mapping.models import ElementKind, Extraction, SemanticElement
from verityflow.models.report import FindingCategory
from verityflow.models.spec import SpecArtifact, SpecDefault, SpecRule
from verityflow.models.workflow import Branch, NodeKind, WorkflowArtifact, WorkflowNode
from verityflow.requirements.compile import compile_document
from verityflow.requirements.parser import parse_requirements

DEV = Path(__file__).resolve().parent.parent.parent / "fixtures" / "dev"
STARTER = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, waiting >= 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
"""


def _load(name: str = "B01_starter") -> tuple[TypedArtifacts, list[SemanticElement]]:
    typed = parse_artifacts(load_bundle(DEV / name))
    assert typed.requirements
    policy, _ = compile_document(typed.requirements[0].document)
    return typed, list(extract_requirements(policy).elements)


def _requirements_from_text(typed: TypedArtifacts, text: str) -> list[SemanticElement]:
    assert typed.dictionary is not None
    document = parse_requirements(text, "requirements.txt", typed.dictionary)
    policy, _ = compile_document(document)
    return list(extract_requirements(policy).elements)


def _spec_result(
    typed: TypedArtifacts,
    requirements: list[SemanticElement],
    *,
    spec: SpecArtifact | None = None,
    config: Config | None = None,
    hints=None,
):
    assert typed.dictionary is not None
    extraction = extract_spec(spec or typed.spec, typed.dictionary, None)
    return map_to_elements(
        tuple(requirements),
        extraction,
        typed.dictionary,
        artifact="spec",
        config=config or Config(),
        hints=hints,
    )


def test_requirements_are_guarded_by_earlier_entries() -> None:
    typed, requirements = _load()
    guards = [element.condition for element in requirements]
    assert guards[0] == typed.spec.rules[0].when  # type: ignore[union-attr]
    assert isinstance(guards[1], And)
    assert any(isinstance(item, NotTrue) for item in guards[1].items)
    assert isinstance(guards[3], And)
    assert all(isinstance(item, NotTrue) for item in guards[3].items)
    assert not any(item == Const(value=True) for item in guards[3].items)


def test_spec_correspondence_is_semantic_and_complete() -> None:
    typed, requirements = _load()
    result = _spec_result(typed, requirements)
    assert result.complete is True
    assert result.findings == ()
    assert [(match.requirement_ref, match.element_id) for match in result.matches] == [
        ("R1", "S1"),
        ("R2", "S2"),
        ("R3", "S3"),
        ("R4", "SPEC:default"),
    ]


def test_workflow_else_branch_matches_the_catch_all() -> None:
    typed, requirements = _load()
    assert typed.workflow is not None and typed.dictionary is not None
    extraction = extract_workflow(typed.workflow, typed.dictionary, None)
    result = map_to_elements(
        tuple(requirements), extraction, typed.dictionary, artifact="workflow", config=Config()
    )
    assert result.complete is True
    assert [match.element_id for match in result.matches] == [
        "start:b1",
        "start:b2",
        "start:b3",
        "start:b4",
    ]


def test_renaming_identifiers_does_not_change_correspondence() -> None:
    typed, requirements = _load()
    assert typed.spec is not None
    renamed = typed.spec.model_copy(
        update={
            "spec_id": "RENAMED",
            "rules": tuple(
                rule.model_copy(update={"id": f"N-{index}"})
                for index, rule in enumerate(typed.spec.rules)
            ),
        }
    )
    result = _spec_result(typed, requirements, spec=renamed)
    assert result.complete is True
    assert [match.element_id for match in result.matches] == [
        "N-0",
        "N-1",
        "N-2",
        "RENAMED:default",
    ]
    assert all(match.method.value == "inferred" for match in result.matches)


def test_reordering_the_decision_list_breaks_equivalence() -> None:
    typed, requirements = _load()
    assert typed.spec is not None
    rules = list(typed.spec.rules)
    first, second = rules[0].priority, rules[1].priority
    rules[0] = rules[0].model_copy(update={"priority": second})
    rules[1] = rules[1].model_copy(update={"priority": first})
    result = _spec_result(
        typed, requirements, spec=typed.spec.model_copy(update={"rules": tuple(rules)})
    )
    assert result.complete is False
    assert "R1" in result.unmatched_requirements
    assert "R2" in result.unmatched_requirements
    assert FindingCategory.UNTRACEABLE_REQUIREMENT in {
        finding.category for finding in result.findings
    }


def test_array_position_does_not_override_explicit_priority() -> None:
    typed, requirements = _load()
    assert typed.spec is not None
    rules = typed.spec.rules
    shuffled = typed.spec.model_copy(update={"rules": (rules[1], rules[0], rules[2])})
    result = _spec_result(typed, requirements, spec=shuffled)
    assert result.complete is True


def test_units_are_normalized_before_correspondence() -> None:
    typed, requirements = _load()
    assert typed.spec is not None
    rules = list(typed.spec.rules)
    third = rules[2]
    assert third.when is not None
    rules[2] = third.model_copy(
        update={"when": third.when.model_copy(update={"value": 1800, "unit": "seconds"})}
    )
    result = _spec_result(
        typed, requirements, spec=typed.spec.model_copy(update={"rules": tuple(rules)})
    )
    assert result.complete is True


def test_different_destination_prevents_a_match() -> None:
    typed, requirements = _load()
    assert typed.spec is not None
    rules = list(typed.spec.rules)
    rules[0] = rules[0].model_copy(update={"destination": "normal_queue"})
    result = _spec_result(
        typed, requirements, spec=typed.spec.model_copy(update={"rules": tuple(rules)})
    )
    assert result.complete is False
    assert "R1" in result.unmatched_requirements


def test_missing_values_are_treated_consistently() -> None:
    typed, _ = _load()
    requirements = _requirements_from_text(
        typed,
        "R1. vip -> priority queue.\nR2. All remaining tickets -> normal queue.\n",
    )
    spec = SpecArtifact(
        spec_id="VIP",
        version="1.0",
        rules=(
            SpecRule(
                id="V1",
                priority=1,
                when=Eq(field="vip", value=True),
                destination="priority_queue",
            ),
        ),
        default=SpecDefault(destination="normal_queue"),
    )
    result = _spec_result(typed, requirements, spec=spec)
    assert result.complete is True


def test_invalid_hint_is_reported_and_ignored() -> None:
    typed, requirements = _load()
    result = _spec_result(typed, requirements, hints={"R1": ("S2",)})
    assert any(
        finding.category is FindingCategory.INVALID_TRACE_HINT for finding in result.findings
    )
    mapping = next(match for match in result.matches if match.requirement_ref == "R1")
    assert mapping.element_id == "S1"
    assert mapping.method.value == "inferred"


def test_unknown_hint_targets_are_reported() -> None:
    typed, requirements = _load()
    result = _spec_result(typed, requirements, hints={"NOPE": ("S1",), "R1": ("NOPE",)})
    categories = {finding.category for finding in result.findings}
    assert FindingCategory.MISSING_EVIDENCE in categories


def _synthetic(condition, destination: str = "priority_queue") -> Extraction:
    return Extraction(
        elements=(
            SemanticElement(
                element_id="A1",
                kind=ElementKind.SPEC_RULE,
                condition=condition.model_copy(),
                destination=destination,
                precedence=1,
            ),
            SemanticElement(
                element_id="A2",
                kind=ElementKind.SPEC_RULE,
                condition=condition.model_copy(),
                destination=destination,
                precedence=2,
            ),
        )
    )


def _ambiguous_requirement() -> SemanticElement:
    return SemanticElement(
        element_id="REQ-X",
        kind=ElementKind.REQUIREMENT,
        condition=Eq(field="vip", value=True),
        destination="priority_queue",
        precedence=0,
    )


def test_ambiguity_is_never_resolved_arbitrarily() -> None:
    typed, _ = _load()
    assert typed.dictionary is not None
    result = map_to_elements(
        (_ambiguous_requirement(),),
        _synthetic(Eq(field="vip", value=True)),
        typed.dictionary,
        artifact="spec",
        config=Config(),
    )
    assert result.complete is False
    assert result.matches == ()
    assert result.unmatched_requirements == ("REQ-X",)
    categories = {finding.category for finding in result.findings}
    assert FindingCategory.AMBIGUOUS_MAPPING in categories
    assert FindingCategory.UNTRACEABLE_REQUIREMENT in categories
    assert FindingCategory.UNTRACEABLE_ELEMENT in categories


def test_a_valid_hint_narrows_ambiguity() -> None:
    typed, _ = _load()
    assert typed.dictionary is not None
    result = map_to_elements(
        (_ambiguous_requirement(),),
        _synthetic(Eq(field="vip", value=True)),
        typed.dictionary,
        artifact="spec",
        config=Config(),
        hints={"REQ-X": ("A2",)},
    )
    assert result.matches[0].element_id == "A2"
    assert result.matches[0].method.value == "hint"


def test_hint_that_still_leaves_two_candidates_is_ambiguous() -> None:
    typed, _ = _load()
    assert typed.dictionary is not None
    result = map_to_elements(
        (_ambiguous_requirement(),),
        _synthetic(Eq(field="vip", value=True)),
        typed.dictionary,
        artifact="spec",
        config=Config(),
        hints={"REQ-X": ("A1", "A2")},
    )
    assert result.matches == ()
    assert FindingCategory.AMBIGUOUS_MAPPING in {finding.category for finding in result.findings}


def test_signature_limit_is_disclosed() -> None:
    typed, requirements = _load()
    result = _spec_result(
        typed, requirements, config=Config(limits=Limits(mapping_max_signature_cells=1))
    )
    assert result.complete is False
    assert any(finding.category is FindingCategory.LIMIT_EXCEEDED for finding in result.findings)


def test_spec_without_an_else_leaves_inputs_unreviewed() -> None:
    typed, requirements = _load()
    assert typed.spec is not None
    spec = typed.spec.model_copy(update={"default": None})
    result = _spec_result(typed, requirements, spec=spec)
    assert result.complete is False
    assert "R4" in result.unmatched_requirements


def test_duplicate_spec_priorities_are_an_ambiguity() -> None:
    typed, requirements = _load()
    assert typed.spec is not None
    rules = list(typed.spec.rules)
    rules[1] = rules[1].model_copy(update={"priority": rules[0].priority})
    extraction = extract_spec(
        typed.spec.model_copy(update={"rules": tuple(rules)}), typed.dictionary, None
    )
    assert extraction.sound is False
    assert extraction.elements == ()
    result = _spec_result(
        typed,
        requirements,
        spec=typed.spec.model_copy(update={"rules": tuple(rules)}),
    )
    assert result.complete is False
    assert any(finding.category is FindingCategory.AMBIGUITY for finding in result.findings)


def test_workflow_without_else_is_incomplete() -> None:
    typed, requirements = _load()
    assert typed.workflow is not None and typed.dictionary is not None
    nodes = list(typed.workflow.nodes)
    start = nodes[0]
    branches = tuple(branch for branch in start.branches if branch.condition is not None)
    nodes[0] = start.model_copy(update={"branches": branches})
    workflow = typed.workflow.model_copy(update={"nodes": tuple(nodes)})
    extraction = extract_workflow(workflow, typed.dictionary, None)
    assert extraction.sound is False
    result = map_to_elements(
        tuple(requirements), extraction, typed.dictionary, artifact="workflow", config=Config()
    )
    assert result.complete is False
    assert "R4" in result.unmatched_requirements


def test_workflow_cycle_is_reported() -> None:
    typed, _ = _load()
    assert typed.dictionary is not None
    workflow = WorkflowArtifact(
        workflow_id="loop",
        version="1.0",
        start="start",
        nodes=(
            WorkflowNode(
                id="start",
                version="1.0",
                kind=NodeKind.DECISION,
                branches=(
                    Branch(
                        branch_id="back",
                        version="1.0",
                        condition=Eq(field="vip", value=True),
                        target="start",
                    ),
                    Branch(branch_id="out", version="1.0", target="done"),
                ),
            ),
            WorkflowNode(
                id="done", version="1.0", kind=NodeKind.TERMINAL, destination="normal_queue"
            ),
        ),
    )
    extraction = extract_workflow(workflow, typed.dictionary, None)
    assert extraction.sound is False
    assert any(finding.category is FindingCategory.STRUCTURAL for finding in extraction.findings)


def test_unmatched_elements_are_reported() -> None:
    typed, requirements = _load()
    assert typed.dictionary is not None
    extra = SemanticElement(
        element_id="GHOST",
        kind=ElementKind.SPEC_RULE,
        condition=Eq(field="vip", value=True),
        destination="billing_queue",
        precedence=9,
    )
    extraction = Extraction(
        elements=(*extract_spec(typed.spec, typed.dictionary, None).elements, extra)
    )
    result = map_to_elements(
        tuple(requirements), extraction, typed.dictionary, artifact="spec", config=Config()
    )
    assert "GHOST" in result.unmatched_elements
    assert FindingCategory.UNTRACEABLE_ELEMENT in {finding.category for finding in result.findings}
