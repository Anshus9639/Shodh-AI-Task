from __future__ import annotations

import pytest
from pydantic import ValidationError

from verityflow.ir.model import And, Const, Eq, Missing, NotNode, NotTrue, Or
from verityflow.models.conditions import ExternalAnd, ExternalNot, ExternalOr, to_ir
from verityflow.models.dictionary import DataDictionary, FieldDecl, FieldType
from verityflow.models.spec import SpecArtifact
from verityflow.models.workflow import Branch, WorkflowArtifact

NOT_TRUE = {"kind": "not_true", "item": {"kind": "const", "value": True}}
EXTERNAL_NOT_TRUE = {"kind": "and", "items": [NOT_TRUE, NOT_TRUE]}
EXTERNAL_NOT_TRUE_NESTED = {
    "kind": "or",
    "items": [{"kind": "not", "item": NOT_TRUE}, {"kind": "const", "value": False}],
}


def _spec(when: object) -> dict[str, object]:
    return {
        "spec_id": "SPEC",
        "version": "1",
        "rules": [{"id": "R", "priority": 1, "when": when, "destination": "d"}],
    }


def _workflow(condition: object) -> dict[str, object]:
    return {
        "workflow_id": "WF",
        "version": "1",
        "start": "start",
        "nodes": [
            {
                "id": "start",
                "version": "1",
                "kind": "decision",
                "branches": [
                    {
                        "branch_id": "b1",
                        "version": "1",
                        "condition": condition,
                        "target": "end",
                    }
                ],
            },
            {"id": "end", "version": "1", "kind": "terminal", "destination": "d"},
        ],
    }


@pytest.mark.parametrize("when", [NOT_TRUE, EXTERNAL_NOT_TRUE, EXTERNAL_NOT_TRUE_NESTED])
def test_spec_artifact_rejects_internal_guard(when: object) -> None:
    with pytest.raises(ValidationError):
        SpecArtifact.model_validate(_spec(when))


@pytest.mark.parametrize("condition", [NOT_TRUE, EXTERNAL_NOT_TRUE])
def test_workflow_artifact_rejects_internal_guard(condition: object) -> None:
    with pytest.raises(ValidationError):
        WorkflowArtifact.model_validate(_workflow(condition))


def test_data_dictionary_rejects_internal_guard() -> None:
    with pytest.raises(ValidationError):
        DataDictionary(
            fields=(FieldDecl(name="category", type=FieldType.STRING),),
            named_predicates={"anything": NOT_TRUE},  # type: ignore[dict-item]
        )


def test_external_conditions_accept_the_public_grammar() -> None:
    spec = SpecArtifact.model_validate(
        _spec(
            {
                "kind": "and",
                "items": [
                    {"kind": "missing", "field": "category"},
                    {
                        "kind": "not",
                        "item": {"kind": "eq", "field": "priority", "value": "urgent"},
                    },
                ],
            }
        )
    )
    when = spec.rules[0].when
    assert isinstance(when, ExternalAnd)
    assert when.items[1] == ExternalNot(item=Eq(field="priority", value="urgent"))


def test_to_ir_rekeys_composites_and_preserves_leaves() -> None:
    condition = ExternalAnd(
        items=(
            Missing(field="category"),
            ExternalNot(item=ExternalOr(items=(Const(value=False), Eq(field="vip", value=True)))),
        )
    )
    expression = to_ir(condition)
    assert expression == And(
        items=(
            Missing(field="category"),
            NotNode(item=Or(items=(Const(value=False), Eq(field="vip", value=True)))),
        )
    )


def test_internal_not_true_is_still_constructible() -> None:
    guard = NotTrue(item=Const(value=True))
    assert guard.kind == "not_true"
    assert isinstance(And(items=(guard, Const(value=True))), And)


def test_branch_condition_uses_the_external_contract() -> None:
    branch = Branch(branch_id="b", version="1", condition=Missing(field="category"), target="t")
    assert branch.condition == Missing(field="category")
    with pytest.raises(ValidationError):
        Branch(
            branch_id="b",
            version="1",
            condition=NotTrue(item=Const(value=True)),  # type: ignore[arg-type]
            target="t",
        )
