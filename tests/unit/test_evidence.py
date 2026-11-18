from __future__ import annotations

import json
from pathlib import Path

import pytest

from verityflow.evidence import SourceCatalog, merge_findings, validate_findings
from verityflow.ingest.loader import load_bundle
from verityflow.models.evidence import EvidenceReference, EvidenceRole
from verityflow.models.report import FindingCategory, Severity, make_finding
from verityflow.verify.review import review_bundle

ROOT = Path(__file__).resolve().parent.parent.parent
BUNDLE = ROOT / "fixtures" / "remediation" / "approvals" / "C_approved_25_impl_30"


@pytest.fixture(scope="module")
def loaded():
    return load_bundle(BUNDLE)


@pytest.fixture(scope="module")
def catalog(loaded) -> SourceCatalog:
    return SourceCatalog.from_loaded(loaded)


@pytest.fixture(scope="module")
def report():
    return review_bundle(BUNDLE)


def _requirement_reference(catalog: SourceCatalog):
    reference = catalog.text_reference("REQ@1.0", 3, 3, EvidenceRole.SOURCE_REQUIREMENT)
    assert reference is not None
    assert "at least 30 minutes" in reference.excerpt
    return reference


def test_reference_serialization_round_trips(catalog: SourceCatalog) -> None:
    reference = _requirement_reference(catalog)
    restored = EvidenceReference.model_validate_json(reference.model_dump_json())
    assert restored == reference
    assert restored.to_location().path == "requirements.txt"


def test_catalog_builds_and_validates_each_anchor_kind(catalog: SourceCatalog) -> None:
    source = _requirement_reference(catalog)
    workflow = catalog.pointer_reference(
        "WF@1.0", "/nodes/0/branches/2", EvidenceRole.WORKFLOW_BRANCH
    )
    approval = catalog.approval_reference("A1")
    change = catalog.change_reference("C1", "1")
    for reference in (source, workflow, approval, change):
        assert reference is not None
        assert catalog.validate(reference) is None


def test_fabricated_excerpt_is_rejected(catalog: SourceCatalog) -> None:
    fabricated = catalog.text_reference(
        "REQ@1.0",
        3,
        3,
        EvidenceRole.SOURCE_REQUIREMENT,
        excerpt="R3. Something that is not in the file.",
    )
    assert fabricated is None


def test_invalid_pointer_and_version_do_not_resolve(catalog: SourceCatalog) -> None:
    assert (
        catalog.pointer_reference("WF@1.0", "/nodes/99/branches", EvidenceRole.WORKFLOW_BRANCH)
        is None
    )
    assert catalog.text_reference("REQ@9.0", 1, 1, EvidenceRole.SOURCE_REQUIREMENT) is None


def test_tampered_reference_is_reported_by_validation(catalog: SourceCatalog, report) -> None:
    source = _source_ref(catalog, report)
    tampered = source.model_copy(update={"source_hash": "0" * 64})
    assert "source hash does not match" in (catalog.validate(tampered) or "")


def _implementation_refs(catalog: SourceCatalog, report):
    code = next(
        finding
        for finding in report.findings
        if finding.category is FindingCategory.THRESHOLD_CHANGED and finding.artifact_id == "ROUTER"
    )
    roles = {reference.evidence_role for reference in code.evidence_refs}
    assert EvidenceRole.PYTHON_CODE in roles
    return tuple(code.evidence_refs)


def _source_ref(catalog: SourceCatalog, report):
    del report
    return _requirement_reference(catalog)


def test_confirmed_finding_requires_a_correction(catalog: SourceCatalog, report) -> None:
    finding = make_finding(
        FindingCategory.THRESHOLD_CHANGED,
        Severity.BLOCKER,
        "threshold differs",
        rule_ref="R3",
        artifact_id="ROUTER",
        artifact_version="1.0",
        evidence_refs=(_source_ref(catalog, report), *_implementation_refs(catalog, report)),
        semantic="test|threshold",
    )
    result = validate_findings([finding], catalog)
    assert result.findings[0].category is FindingCategory.MISSING_EVIDENCE
    assert result.unreviewed
    assert "recommended correction" in result.unreviewed[0].description


def test_behavioural_blocker_needs_implementation_evidence(catalog: SourceCatalog, report) -> None:
    finding = make_finding(
        FindingCategory.THRESHOLD_CHANGED,
        Severity.BLOCKER,
        "threshold differs",
        rule_ref="R3",
        artifact_id="ROUTER",
        artifact_version="1.0",
        evidence_refs=(_source_ref(catalog, report),),
        semantic="test|threshold",
        recommendation="Align the router with the confirmed threshold.",
    )
    result = validate_findings([finding], catalog)
    assert result.findings[0].category is FindingCategory.MISSING_EVIDENCE
    assert "implementation-side evidence" in result.unreviewed[0].description


def test_omission_is_valid_with_requirement_evidence_only(catalog: SourceCatalog, report) -> None:
    finding = make_finding(
        FindingCategory.MISSING_EXCEPTION,
        Severity.BLOCKER,
        "the exception is absent",
        rule_ref="R3",
        artifact_id="SPEC",
        artifact_version="1.0",
        evidence_refs=(_source_ref(catalog, report),),
        semantic="test|missing-exception",
        recommendation="Add the missing exception branch; no target location exists to cite.",
    )
    result = validate_findings([finding], catalog)
    assert result.findings[0].severity is Severity.BLOCKER
    assert not result.unreviewed


def test_model_observation_cannot_remain_a_blocker(catalog: SourceCatalog, report) -> None:
    finding = make_finding(
        FindingCategory.THRESHOLD_CHANGED,
        Severity.BLOCKER,
        "model suspects a threshold",
        rule_ref="R3",
        artifact_id="ROUTER",
        artifact_version="1.0",
        evidence_refs=(_source_ref(catalog, report), *_implementation_refs(catalog, report)),
        semantic="test|model",
        recommendation="Align the router.",
    ).model_copy(update={"source": "model"})
    result = validate_findings([finding], catalog)
    assert result.findings[0].severity is Severity.COVERAGE_GAP
    assert result.unreviewed[0].kind == "model"


def test_dedupe_merges_same_defect_and_unions_evidence(catalog: SourceCatalog, report) -> None:
    source = _source_ref(catalog, report)
    implementation = _implementation_refs(catalog, report)
    first = make_finding(
        FindingCategory.THRESHOLD_CHANGED,
        Severity.BLOCKER,
        "threshold differs at 25",
        rule_ref="R3",
        artifact_id="ROUTER",
        artifact_version="1.0",
        evidence_refs=(source,),
        semantic="threshold|R3|>=|25",
        recommendation="Align the router.",
    )
    second = make_finding(
        FindingCategory.THRESHOLD_CHANGED,
        Severity.BLOCKER,
        "threshold differs at 25 (second witness)",
        rule_ref="R3",
        artifact_id="ROUTER",
        artifact_version="1.0",
        evidence_refs=implementation,
        semantic="threshold|R3|>=|25",
        recommendation="Align the router.",
    )
    merged = merge_findings([first, second])
    assert len(merged) == 1
    assert {reference.anchor_value for reference in merged[0].evidence_refs} == {
        source.anchor_value,
        *(reference.anchor_value for reference in implementation),
    }
    assert any(
        reference.evidence_role is EvidenceRole.SOURCE_REQUIREMENT
        for reference in merged[0].evidence_refs
    )
    assert any(
        reference.evidence_role is EvidenceRole.PYTHON_CODE for reference in merged[0].evidence_refs
    )


def test_dedupe_keeps_distinct_defects_on_one_rule(catalog: SourceCatalog, report) -> None:
    source = _source_ref(catalog, report)
    implementation = _implementation_refs(catalog, report)
    threshold = make_finding(
        FindingCategory.THRESHOLD_CHANGED,
        Severity.BLOCKER,
        "threshold differs",
        rule_ref="R3",
        artifact_id="ROUTER",
        artifact_version="1.0",
        evidence_refs=(source, *implementation),
        semantic="threshold|R3|>=|25",
        recommendation="Align the boundary.",
    )
    destination = make_finding(
        FindingCategory.WRONG_DESTINATION,
        Severity.BLOCKER,
        "destination differs",
        rule_ref="R3",
        artifact_id="ROUTER",
        artifact_version="1.0",
        evidence_refs=(source, *implementation),
        semantic="destination|R3|escalation",
        recommendation="Align the destination.",
    )
    assert len(merge_findings([threshold, destination])) == 2


def test_every_remediation_blocker_has_resolvable_evidence(catalog: SourceCatalog, report) -> None:
    for finding in report.findings:
        if finding.severity is not Severity.BLOCKER:
            continue
        assert finding.location is not None, finding.finding_id
        assert finding.recommendation, finding.finding_id
        assert finding.evidence_refs, finding.finding_id
        for reference in finding.evidence_refs:
            assert catalog.validate(reference) is None, json.dumps(
                reference.model_dump(mode="json"), indent=2
            )
        roles = {reference.evidence_role for reference in finding.evidence_refs}
        if finding.category in {
            FindingCategory.THRESHOLD_CHANGED,
            FindingCategory.WRONG_OPERATOR,
            FindingCategory.WRONG_DESTINATION,
            FindingCategory.PRECEDENCE,
            FindingCategory.RUNTIME_ERROR,
            FindingCategory.SCRIPT_DISAGREEMENT,
        }:
            assert EvidenceRole.SOURCE_REQUIREMENT in roles
            assert roles & {
                EvidenceRole.SPECIFICATION,
                EvidenceRole.WORKFLOW_BRANCH,
                EvidenceRole.PYTHON_CODE,
            }
