from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from support import Stage1Result, run_stage1

from verityflow.ir.model import And, Cmp, Expr, NotNode, NotTrue, Or
from verityflow.models.report import FindingCategory, Severity

pytestmark = pytest.mark.holdout

ROOT = Path(__file__).resolve().parent.parent.parent
HOLDOUT = ROOT / "holdout"
BUNDLES = HOLDOUT / "bundles"
LABELS = HOLDOUT / "labels"

_CASES = sorted(path.stem for path in LABELS.glob("*.json"))


def _label(name: str) -> dict[str, Any]:
    return json.loads((LABELS / f"{name}.json").read_text(encoding="utf-8"))


def _thresholds(expr: Expr) -> list[Cmp]:
    found: list[Cmp] = []
    if isinstance(expr, Cmp):
        found.append(expr)
    elif isinstance(expr, (And, Or)):
        for item in expr.items:
            found.extend(_thresholds(item))
    elif isinstance(expr, (NotNode, NotTrue)):
        found.extend(_thresholds(expr.item))
    return found


def _rule_thresholds(result: Stage1Result) -> dict[str, str]:
    confirmed = result.effective.confirmed()
    assert confirmed is not None
    thresholds: dict[str, str] = {}
    for rule in confirmed.rules:
        for cmp in _thresholds(rule.condition):
            thresholds[rule.rule_ref] = str(cmp.value)
    return thresholds


def _finding_categories(result: Stage1Result) -> list[str]:
    return sorted({finding.category.value for finding in result.findings})


@pytest.mark.parametrize("name", _CASES)
def test_holdout_expectations(name: str) -> None:
    expected = _label(name)["expected"]
    result = run_stage1(BUNDLES / name)
    document = result.typed.requirements[0].document

    parse = expected["parse"]
    assert len(result.base_policy.rules) == parse["rules"]
    parse_gap = any(
        issue.severity in {Severity.BLOCKER, Severity.COVERAGE_GAP} for issue in document.issues
    )
    assert parse_gap is parse["gap"]

    mapping = expected["mapping"]
    assert result.mappings["spec"].complete is mapping["spec_complete"]
    assert result.mappings["workflow"].complete is mapping["workflow_complete"]

    resolution = expected["resolution"]
    assert result.effective.confirmed_candidate_id == resolution["confirmed_candidate_id"]
    assert len(result.effective.undetermined) == len(resolution["undetermined"])
    if "applied" in resolution:
        assert list(result.effective.applied) == resolution["applied"]
    for change_id, state in resolution.get("decision_states", {}).items():
        decisions = [
            decision for decision in result.effective.decisions if decision.change_id == change_id
        ]
        assert decisions and decisions[0].state.value == state
    if "rule_thresholds" in resolution:
        assert _rule_thresholds(result) == resolution["rule_thresholds"]

    assert _finding_categories(result) == sorted(expected["finding_categories"])


def test_every_holdout_label_has_a_bundle() -> None:
    assert {path.name for path in BUNDLES.iterdir()} == set(_CASES)


def test_holdout_findings_never_use_an_unknown_category() -> None:
    known = {category.value for category in FindingCategory}
    for name in _CASES:
        for finding in run_stage1(BUNDLES / name).findings:
            assert finding.category.value in known
