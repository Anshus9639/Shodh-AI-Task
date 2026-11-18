"""Development-only evaluation for the post-evaluation remediation sprint.

This runner executes only the 27 Stage 4 development bundles and the 22 new
remediation development bundles. Sealed cases are never discovered or executed
here; ``_guard`` refuses any case path outside those two label directories.

Outputs are written under ``reports/remediation`` only. The retained Stage 4
reports are hashed before and after the run and are never rewritten; their
figures are copied in as the immutable baseline.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evaluation"))
sys.path.insert(0, str(ROOT / "src"))

import harness  # noqa: E402

from verityflow.evaluation.models import ReportHeader  # noqa: E402
from verityflow.schemas import build_schemas  # noqa: E402

CONFIG = ROOT / "configs" / "default.toml"
STAGE4_CASES = ROOT / "evaluation" / "cases"
REMEDIATION_CASES = ROOT / "evaluation" / "remediation" / "cases"
RETAINED = ROOT / "reports" / "final"
OUT = ROOT / "reports" / "remediation"
MODEL_MODE = "off (deterministic; no model requests)"
UNSUPPORTED = frozenset({"UNSUPPORTED_LANGUAGE", "UNSUPPORTED_CODE"})
IMPLEMENTATION_ROLES = frozenset({"SPECIFICATION", "WORKFLOW_BRANCH", "PYTHON_CODE"})
APPROVAL_CASES = tuple(
    "A_original_30 B_approved_25_impl_25 C_approved_25_impl_30 "
    "D_unapproved_25_impl_30 E_wrong_revision_binding F_wrong_operator "
    "G_unit_conversion H_sequential_changes I_conflicting_disputed_impl "
    "J_conflict_plus_independent_defect K_multiple_conflicts "
    "L_valid_supersession M_invalid_approval_hash "
    "N_conflict_plus_runtime_error".split()
)
CONFLICT_CASES = (
    "I_conflicting_disputed_impl",
    "J_conflict_plus_independent_defect",
    "K_multiple_conflicts",
    "N_conflict_plus_runtime_error",
)


def _guard(directory: Path) -> None:
    resolved = directory.resolve()
    allowed = {STAGE4_CASES.resolve(), REMEDIATION_CASES.resolve()}
    if resolved not in allowed:
        raise SystemExit(f"refusing to execute unapproved case directory: {resolved}")
    if "holdout" in str(resolved).casefold() or "sealed" in str(resolved).casefold():
        raise SystemExit("refusing to execute sealed cases")


def _digest_directory(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.json")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _retained_hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256((RETAINED / name).read_bytes()).hexdigest()
        for name in (
            "REPORT.md",
            "counterexamples.json",
            "coverage.json",
            "findings.json",
            "metrics.json",
        )
    }


def _header(report: str, directory: Path, bundle_count: int) -> ReportHeader:
    return ReportHeader(
        report=report,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        code_revision=harness.git_revision(ROOT),
        config_sha256=harness.file_sha256(CONFIG),
        dataset_manifest_sha256=_digest_directory(directory),
        bundle_count=bundle_count,
        model_execution_mode=MODEL_MODE,
        unsupported_scope=(
            "real-model semantic performance is not measured by this report",
            "sealed holdout cases are not executed or referenced by this report",
        ),
    )


def _run(cases: list[harness.Case]) -> list[harness.ReviewRun]:
    runner = harness.Runner("remediation", ROOT / "src", supports_model_flag=True)
    return [harness.run_review(runner, case, CONFIG, model_mode="off") for case in cases]


def _location_resolves(location: dict[str, Any] | None, bundle: Path) -> bool:
    return bool(location) and harness._location_resolves(location, bundle)


def _sha_matches(reference: dict[str, Any], bundle: Path) -> bool:
    path = bundle / str(reference.get("path", ""))
    if not path.is_file():
        return False
    return hashlib.sha256(path.read_bytes()).hexdigest() == reference.get("source_hash")


def _validated_source(reference: dict[str, Any], bundle: Path) -> bool:
    return (
        reference.get("evidence_role") == "SOURCE_REQUIREMENT"
        and _sha_matches(reference, bundle)
        and reference.get("excerpt", "").strip() != ""
    )


def _validated_implementation(reference: dict[str, Any], bundle: Path) -> bool:
    return reference.get("evidence_role") in IMPLEMENTATION_ROLES and _sha_matches(
        reference, bundle
    )


def _finding_rows(run: harness.ReviewRun) -> list[dict[str, Any]]:
    return list((run.report or {}).get("findings", ()))


def _blockers(run: harness.ReviewRun) -> list[dict[str, Any]]:
    return [item for item in _finding_rows(run) if item.get("severity") == "BLOCKER"]


def _duplicate_identity(finding: dict[str, Any]) -> tuple[Any, ...]:
    location = finding.get("location") or {}
    anchors = tuple(
        sorted(
            (
                reference.get("evidence_role"),
                reference.get("artifact_id"),
                reference.get("anchor_type"),
                reference.get("anchor_value"),
            )
            for reference in finding.get("evidence_refs", ())
        )
    )
    return (
        finding.get("category"),
        finding.get("rule_ref"),
        finding.get("artifact_id"),
        finding.get("artifact_version"),
        location.get("path"),
        location.get("line_start"),
        location.get("line_end"),
        location.get("pointer"),
        anchors,
    )


def _finding_quality(cases: list[harness.Case], runs: list[harness.ReviewRun]) -> dict[str, Any]:
    blockers = [
        (case, finding) for case, run in zip(cases, runs, strict=True) for finding in _blockers(run)
    ]
    gaps = [
        finding
        for run in runs
        for finding in _finding_rows(run)
        if finding.get("severity") == "COVERAGE_GAP"
    ]
    infos = [
        finding
        for run in runs
        for finding in _finding_rows(run)
        if finding.get("severity") == "INFO"
    ]
    counterexamples = [
        item
        for run in runs
        for comparison in (run.report or {}).get("comparisons", ())
        for item in comparison.get("counterexamples", ())
    ]
    duplicates = 0
    for run in runs:
        identities = Counter(_duplicate_identity(item) for item in _blockers(run))
        duplicates += sum(count - 1 for count in identities.values() if count > 1)
    false_blockers = sum(
        1
        for case, run in zip(cases, runs, strict=True)
        if case.expected_status == "PASS" and _blockers(run)
    )
    located = sum(
        1
        for case, finding in blockers
        if _location_resolves(finding.get("location"), case.bundle_path)
    )
    source = sum(
        1
        for case, finding in blockers
        if any(_validated_source(ref, case.bundle_path) for ref in finding.get("evidence_refs", ()))
    )
    implementation = sum(
        1
        for case, finding in blockers
        if any(
            _validated_implementation(ref, case.bundle_path)
            for ref in finding.get("evidence_refs", ())
        )
    )
    corrections = sum(1 for _, finding in blockers if (finding.get("recommendation") or "").strip())
    witnesses = sum(
        1
        for _, finding in blockers
        if harness._WITNESS_PATTERN.search(str(finding.get("message", "")))
    )
    return {
        "confirmed_findings": len(blockers),
        "disclosed_gap_findings": len(gaps),
        "info_findings": len(infos),
        "structured_location": {"numerator": located, "denominator": len(blockers)},
        "validated_source_evidence": {"numerator": source, "denominator": len(blockers)},
        "validated_implementation_evidence": {
            "numerator": implementation,
            "denominator": len(blockers),
        },
        "corrections": {"numerator": corrections, "denominator": len(blockers)},
        "message_witness": {"numerator": witnesses, "denominator": len(blockers)},
        "valid_counterexamples": {
            "numerator": sum(1 for item in counterexamples if item.get("validated") is True),
            "denominator": len(counterexamples),
        },
        "duplicate_findings": duplicates,
        "unsupported_findings": sum(
            1 for run in runs for item in _finding_rows(run) if item.get("category") in UNSUPPORTED
        ),
        "false_blockers_on_expected_pass_cases": {
            "numerator": false_blockers,
            "denominator": sum(1 for case in cases if case.expected_status == "PASS"),
        },
    }


def _label_extras() -> dict[str, dict[str, Any]]:
    extras: dict[str, dict[str, Any]] = {}
    for path in sorted(REMEDIATION_CASES.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        extras[payload["bundle_id"]] = payload["expected"]
    return extras


def _approval_table(cases: list[harness.Case], runs: list[harness.ReviewRun]) -> dict[str, Any]:
    extras = _label_extras()
    rows: list[dict[str, Any]] = []
    applicable = correct_applicable = conflict_ok = 0
    for case, run in zip(cases, runs, strict=True):
        if case.bundle_id not in APPROVAL_CASES:
            continue
        report = run.report or {}
        expected = extras[case.bundle_id]
        expected_applied = expected.get("applied_changes")
        actual_applied = list(report.get("applied_changes", ()))
        applied_ok: bool | None = None
        if expected_applied is not None:
            applicable += 1
            applied_ok = actual_applied == list(expected_applied)
            correct_applicable += int(applied_ok)
        categories = {item.get("category") for item in _finding_rows(run)}
        conflicts = list(report.get("conflict_analysis", ()))
        conflict_correct: bool | None = None
        if case.bundle_id in CONFLICT_CASES:
            conflict_correct = (
                run.status == case.expected_status
                and report.get("confirmed_candidate_id") == "confirmed"
                and bool(conflicts)
                and all(
                    item.get("affected_rule")
                    and item.get("conflicting_approval_ids")
                    and item.get("diagnostic_variants")
                    for item in conflicts
                )
            )
            conflict_ok += int(conflict_correct)
        rows.append(
            {
                "bundle_id": case.bundle_id,
                "expected_status": case.expected_status,
                "actual_status": run.status,
                "status_correct": run.status == case.expected_status,
                "expected_applied_changes": expected_applied,
                "actual_applied_changes": actual_applied,
                "applied_changes_correct": applied_ok,
                "expected_effective_threshold": expected.get("effective_threshold"),
                "expected_defect_classes": list(case.defect_classes),
                "actual_blocker_categories": sorted(
                    item.get("category") for item in _blockers(run)
                ),
                "disclosed_categories": sorted(
                    item.get("category")
                    for item in _finding_rows(run)
                    if item.get("severity") == "COVERAGE_GAP"
                ),
                "expected_primary": case.primary_defect,
                "primary_present": (
                    case.primary_defect is None or case.primary_defect in categories
                ),
                "conflict_analysis_entries": len(conflicts),
                "conflict_handling_correct": conflict_correct,
                "error": run.error,
            }
        )
    return {
        "cases": rows,
        "summary": {
            "cases": len(rows),
            "status_correct": {
                "numerator": sum(1 for row in rows if row["status_correct"]),
                "denominator": len(rows),
            },
            "applied_changes_correct": {
                "numerator": correct_applicable,
                "denominator": applicable,
            },
            "conflict_handling_correct": {
                "numerator": conflict_ok,
                "denominator": len(CONFLICT_CASES),
            },
        },
    }


def _semantic_table(cases: list[harness.Case], runs: list[harness.ReviewRun]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case, run in zip(cases, runs, strict=True):
        if not case.bundle_id.startswith("S"):
            continue
        blockers = _blockers(run)
        all_witnesses = [
            item
            for comparison in (run.report or {}).get("comparisons", ())
            for item in comparison.get("counterexamples", ())
        ]
        witnesses = [item for item in all_witnesses if item.get("validated") is True]
        categories = {item.get("category") for item in blockers}
        primary_correct = case.primary_defect is None or case.primary_defect in categories
        trace_present = all(
            item.get("requirement_trace")
            and (item.get("artifact_trace") or "error:" in item.get("artifact_outcome", ""))
            for item in witnesses
        )
        evidence_ok = all(
            any(
                ref.get("evidence_role") == "SOURCE_REQUIREMENT"
                for ref in item.get("evidence_refs", ())
            )
            and any(
                ref.get("evidence_role") in IMPLEMENTATION_ROLES
                for ref in item.get("evidence_refs", ())
            )
            for item in blockers
        )
        rows.append(
            {
                "bundle_id": case.bundle_id,
                "scenarios": list(case.scenarios),
                "expected_primary": case.primary_defect,
                "actual_blocker_categories": sorted(categories),
                "primary_correct": primary_correct,
                "diagnosis_is_specific": "SCRIPT_DISAGREEMENT" not in categories,
                "confirmed_findings": len(blockers),
                "validated_counterexamples": len(witnesses),
                "total_counterexamples": len(all_witnesses),
                "traces_present": trace_present,
                "evidence_on_both_sides": evidence_ok,
                "status": run.status,
                "error": run.error,
            }
        )
    return {
        "cases": rows,
        "summary": {
            "cases": len(rows),
            "primary_correct": {
                "numerator": sum(1 for row in rows if row["primary_correct"]),
                "denominator": len(rows),
            },
            "specific_diagnosis": {
                "numerator": sum(1 for row in rows if row["diagnosis_is_specific"]),
                "denominator": len(rows),
            },
            "validated_counterexamples": {
                "numerator": sum(row["validated_counterexamples"] for row in rows),
                "denominator": sum(row["total_counterexamples"] for row in rows),
            },
            "traces_present": {
                "numerator": sum(1 for row in rows if row["traces_present"]),
                "denominator": len(rows),
            },
            "evidence_on_both_sides": {
                "numerator": sum(1 for row in rows if row["evidence_on_both_sides"]),
                "denominator": len(rows),
            },
        },
    }


def _ratio_text(ratio: dict[str, Any]) -> str:
    return f"{ratio['numerator']}/{ratio['denominator']}"


def _evidence_comparison(
    retained: dict[str, Any],
    retained_findings: dict[str, Any],
    stage4_metrics: Any,
    remediation_metrics: Any,
    stage4_quality: dict[str, Any],
    remediation_quality: dict[str, Any],
) -> dict[str, Any]:
    """Compare the retained baseline, the Stage 4 re-run and remediation development.

    The retained report stores no evidence references, so source/implementation
    evidence and corrections are reported as not measured rather than as zero.
    """
    baseline = retained["finding_quality"]
    retained_rows = retained_findings.get("cases", ())
    retained_total = sum(len(row.get("findings", ())) for row in retained_rows)
    retained_blockers = sum(
        1
        for row in retained_rows
        for finding in row.get("findings", ())
        if finding.get("severity") == "BLOCKER"
    )
    return {
        "decisions": {
            "retained": retained["decision"]["overall_correct"],
            "stage4_dev": stage4_metrics.decision.overall_correct.model_dump(),
            "remediation_dev": remediation_metrics.decision.overall_correct.model_dump(),
        },
        "false_block": {
            "retained": retained["decision"]["false_block"],
            "stage4_dev": stage4_metrics.decision.false_block.model_dump(),
            "remediation_dev": remediation_metrics.decision.false_block.model_dump(),
        },
        "denominator_note": (
            "Retained counting scores one identifying finding per defective case; "
            "post-remediation runs score every BLOCKER after duplicate merging, so "
            "denominators differ where duplicate omission findings were merged"
        ),
        "retained_stage4": {
            "decisions_correct": _ratio_text(retained["decision"]["overall_correct"]),
            "findings_total": retained_total,
            "blockers": f"{retained_blockers} of {retained_total} findings",
            "structured_locations": _ratio_text(baseline["evidence_location"]),
            "structured_evidence": _ratio_text(baseline["structured_evidence"]),
            "validated_source_evidence": "not measured (no evidence items)",
            "validated_implementation_evidence": "not measured (no evidence items)",
            "recommended_corrections": "not measured (field absent)",
            "message_witness": _ratio_text(baseline["message_witness"]),
            "valid_counterexamples": _ratio_text(baseline["valid_counterexamples"]),
            "duplicate_findings": baseline["duplicate_findings"],
            "unsupported_findings": baseline["unsupported_findings"],
        },
        "stage4_dev_rerun": {
            "confirmed_findings": stage4_quality["confirmed_findings"],
            "structured_locations": _ratio_text(stage4_quality["structured_location"]),
            "validated_source_evidence": _ratio_text(stage4_quality["validated_source_evidence"]),
            "validated_implementation_evidence": _ratio_text(
                stage4_quality["validated_implementation_evidence"]
            ),
            "recommended_corrections": _ratio_text(stage4_quality["corrections"]),
            "message_witness": _ratio_text(stage4_quality["message_witness"]),
            "valid_counterexamples": _ratio_text(stage4_quality["valid_counterexamples"]),
            "duplicate_findings": stage4_quality["duplicate_findings"],
            "unsupported_findings": stage4_quality["unsupported_findings"],
            "false_blockers_on_expected_pass_cases": _ratio_text(
                stage4_quality["false_blockers_on_expected_pass_cases"]
            ),
        },
        "remediation_dev": {
            "confirmed_findings": remediation_quality["confirmed_findings"],
            "structured_locations": _ratio_text(remediation_quality["structured_location"]),
            "validated_source_evidence": _ratio_text(
                remediation_quality["validated_source_evidence"]
            ),
            "validated_implementation_evidence": _ratio_text(
                remediation_quality["validated_implementation_evidence"]
            ),
            "recommended_corrections": _ratio_text(remediation_quality["corrections"]),
            "message_witness": _ratio_text(remediation_quality["message_witness"]),
            "valid_counterexamples": _ratio_text(remediation_quality["valid_counterexamples"]),
            "duplicate_findings": remediation_quality["duplicate_findings"],
            "unsupported_findings": remediation_quality["unsupported_findings"],
            "false_blockers_on_expected_pass_cases": _ratio_text(
                remediation_quality["false_blockers_on_expected_pass_cases"]
            ),
        },
    }


def _evidence_table(comparison: dict[str, Any]) -> list[str]:
    baseline = comparison["retained_stage4"]
    rerun = comparison["stage4_dev_rerun"]
    remediation = comparison["remediation_dev"]
    rows = (
        (
            "Decisions correct",
            _ratio_text(comparison["decisions"]["retained"]),
            _ratio_text(comparison["decisions"]["stage4_dev"]),
            _ratio_text(comparison["decisions"]["remediation_dev"]),
        ),
        (
            "Confirmed BLOCKER findings",
            baseline["blockers"],
            str(rerun["confirmed_findings"]),
            str(remediation["confirmed_findings"]),
        ),
        (
            "Structured, resolving locations",
            baseline["structured_locations"],
            rerun["structured_locations"],
            remediation["structured_locations"],
        ),
        (
            "Validated source evidence",
            baseline["validated_source_evidence"],
            rerun["validated_source_evidence"],
            remediation["validated_source_evidence"],
        ),
        (
            "Validated implementation evidence",
            baseline["validated_implementation_evidence"],
            rerun["validated_implementation_evidence"],
            remediation["validated_implementation_evidence"],
        ),
        (
            "Recommended corrections",
            baseline["recommended_corrections"],
            rerun["recommended_corrections"],
            remediation["recommended_corrections"],
        ),
        (
            "Message witness",
            baseline["message_witness"],
            rerun["message_witness"],
            remediation["message_witness"],
        ),
        (
            "Validated counterexamples",
            baseline["valid_counterexamples"],
            rerun["valid_counterexamples"],
            remediation["valid_counterexamples"],
        ),
        (
            "Duplicate findings",
            str(baseline["duplicate_findings"]),
            str(rerun["duplicate_findings"]),
            str(remediation["duplicate_findings"]),
        ),
        (
            "False blockers on expected-PASS",
            _ratio_text(comparison["false_block"]["retained"]),
            rerun["false_blockers_on_expected_pass_cases"],
            remediation["false_blockers_on_expected_pass_cases"],
        ),
    )
    lines = [
        "| Measure | Stage 4 retained (pre-remediation) | Stage 4 dev re-run "
        "(post-remediation code) | Remediation dev |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(f"| {name} | {left} | {middle} | {right} |" for name, left, middle, right in rows)
    lines.extend(
        [
            "",
            f"Denominators are not identical. {comparison['denominator_note']}.",
            "The retained Stage 4 reports were hashed before and after this run and "
            "were not modified.",
        ]
    )
    return lines


def _metrics_payload(
    header: ReportHeader,
    cases: list[harness.Case],
    runs: list[harness.ReviewRun],
    *,
    approval: dict[str, Any],
    semantic: dict[str, Any],
    retained: dict[str, Any],
    retained_hashes: dict[str, str],
) -> dict[str, Any]:
    metrics = harness.compute_metrics(header, cases, runs).model_dump(mode="json")
    return {
        "header": header.model_dump(mode="json"),
        "decision": metrics["decision"],
        "finding_quality": _finding_quality(cases, runs),
        "harness_finding_metrics": metrics["finding_quality"],
        "coverage": metrics["coverage"],
        "resources": metrics["resources"],
        "approval_resolution": approval["summary"],
        "conflict_handling": approval["summary"]["conflict_handling_correct"],
        "semantic_counterexamples": semantic["summary"],
        "retained_stage4_baseline": {
            "source": "reports/final/metrics.json",
            "code_revision": retained["header"]["code_revision"],
            "decision": retained["decision"],
            "finding_quality": retained["finding_quality"],
            "coverage": retained["coverage"],
            "reports_sha256": retained_hashes,
            "retained_unchanged": True,
        },
        "notes": (
            "sealed holdouts were not executed; remediation development accuracy is not "
            "unseen-test accuracy; the original Stage 4 report remains the retained baseline"
        ),
    }


def _write(name: str, payload: Any) -> Path:
    path = OUT / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def _validate_findings(cases: list[harness.Case], runs: list[harness.ReviewRun]) -> dict[str, Any]:
    schemas = build_schemas()
    finding_validator = jsonschema.Draft202012Validator(schemas["finding"])
    conflict_validator = jsonschema.Draft202012Validator(schemas["conflict_analysis"])
    rows: list[dict[str, Any]] = []
    total = valid = 0
    for case, run in zip(cases, runs, strict=True):
        report = run.report or {}
        case_findings: list[dict[str, Any]] = []
        for finding in report.get("findings", ()):
            total += 1
            errors = [error.message for error in finding_validator.iter_errors(finding)]
            valid += int(not errors)
            case_findings.append({"finding": finding, "schema_errors": errors})
        case_conflicts: list[dict[str, Any]] = []
        for entry in report.get("conflict_analysis", ()):
            total += 1
            errors = [error.message for error in conflict_validator.iter_errors(entry)]
            valid += int(not errors)
            case_conflicts.append({"conflict_analysis": entry, "schema_errors": errors})
        rows.append(
            {
                "bundle_id": case.bundle_id,
                "expected_status": case.expected_status,
                "actual_status": run.status,
                "error": run.error,
                "findings": case_findings,
                "conflict_analysis": case_conflicts,
            }
        )
    return {
        "schema_validation": {"numerator": valid, "denominator": total},
        "cases": rows,
    }


def main() -> int:
    _guard(STAGE4_CASES)
    _guard(REMEDIATION_CASES)
    OUT.mkdir(parents=True, exist_ok=True)
    before = _retained_hashes()

    retained_metrics = json.loads((RETAINED / "metrics.json").read_text(encoding="utf-8"))
    retained_findings = json.loads((RETAINED / "findings.json").read_text(encoding="utf-8"))

    stage4_cases = harness.load_cases(ROOT, STAGE4_CASES)
    remediation_cases = harness.load_cases(ROOT, REMEDIATION_CASES)
    stage4_runs = _run(stage4_cases)
    remediation_runs = _run(remediation_cases)

    stage4_header = _header("remediation_stage4_dev", STAGE4_CASES, len(stage4_cases))
    remediation_header = _header("remediation_dev", REMEDIATION_CASES, len(remediation_cases))
    stage4_metrics = harness.compute_metrics(stage4_header, stage4_cases, stage4_runs)
    remediation_metrics = harness.compute_metrics(
        remediation_header, remediation_cases, remediation_runs
    )
    approval = _approval_table(remediation_cases, remediation_runs)
    semantic = _semantic_table(remediation_cases, remediation_runs)
    stage4_quality = _finding_quality(stage4_cases, stage4_runs)
    quality = _finding_quality(remediation_cases, remediation_runs)
    evidence = _evidence_comparison(
        retained_metrics,
        retained_findings,
        stage4_metrics,
        remediation_metrics,
        stage4_quality,
        quality,
    )

    after = _retained_hashes()
    if before != after:
        raise SystemExit("retained Stage 4 reports changed during the remediation run")

    _write(
        "baseline_findings.json",
        {
            "header": {
                "report": "remediation_baseline",
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "source": "reports/final/findings.json",
                "retained_code_revision": retained_metrics["header"]["code_revision"],
                "retained_unchanged": True,
                "reports_sha256": before,
                "method": "copied from the retained Stage 4 report; not re-executed",
            },
            "stage4_dev": retained_findings,
            "stage4_metrics": {
                "decision": retained_metrics["decision"],
                "finding_quality": retained_metrics["finding_quality"],
                "coverage": retained_metrics["coverage"],
            },
        },
    )

    final = _validate_findings(remediation_cases, remediation_runs)
    _write(
        "final_findings.json",
        {
            "header": {
                "report": "remediation_final_findings",
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "code_revision": harness.git_revision(ROOT),
                "bundle_count": len(remediation_cases),
                "schema_validation": final["schema_validation"],
            },
            "schema_validation": final["schema_validation"],
            "cases": final["cases"],
        },
    )

    _write("approval_tests.json", approval)
    _write(
        "counterexample_tests.json",
        {
            "baseline_stage4": retained_metrics["finding_quality"]["valid_counterexamples"],
            "remediation": semantic,
        },
    )
    _write(
        "metrics.json",
        _metrics_payload(
            remediation_header,
            remediation_cases,
            remediation_runs,
            approval=approval,
            semantic=semantic,
            retained=retained_metrics,
            retained_hashes=before,
        )
        | {"approval_tests": approval["summary"]},
    )
    _write(
        "development_evaluation.json",
        {
            "header": {
                "report": "remediation_development_comparison",
                "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "code_revision": harness.git_revision(ROOT),
                "model_execution_mode": MODEL_MODE,
            },
            "retained_stage4": {
                "path": "reports/final/metrics.json",
                "code_revision": retained_metrics["header"]["code_revision"],
                "decision": retained_metrics["decision"],
                "finding_quality": retained_metrics["finding_quality"],
                "reports_sha256": before,
            },
            "stage4_dev_rerun": stage4_metrics.model_dump(mode="json"),
            "remediation_dev": remediation_metrics.model_dump(mode="json"),
            "remediation_finding_quality": quality,
            "comparison": {
                "stage4_dev_decision_correct": stage4_metrics.decision.overall_correct.model_dump(),
                "remediation_dev_decision_correct": (
                    remediation_metrics.decision.overall_correct.model_dump()
                ),
                "stage4_dev_structured_evidence": (
                    stage4_metrics.finding_quality.structured_evidence.model_dump()
                ),
                "remediation_dev_structured_evidence": quality["validated_source_evidence"],
                "stage4_dev_valid_counterexamples": (
                    stage4_metrics.finding_quality.valid_counterexamples.model_dump()
                ),
                "remediation_dev_valid_counterexamples": quality["valid_counterexamples"],
                "evidence_coverage": evidence,
                "note": (
                    "Stage 4 development results are retained and re-executed only to measure "
                    "the same development set; remediation development results are not "
                    "unseen-test accuracy"
                ),
            },
        },
    )

    summary = [
        "# Post-evaluation remediation development summary",
        "",
        f"Generated from {len(remediation_cases)} remediation development cases and "
        f"{len(stage4_cases)} Stage 4 development cases. Sealed holdouts were not executed.",
        "",
        "## Evidence coverage before and after",
        "",
        *_evidence_table(evidence),
        "",
        "## Decision accuracy",
        "",
        f"- Remediation development: "
        f"{remediation_metrics.decision.overall_correct.numerator}/"
        f"{remediation_metrics.decision.overall_correct.denominator}",
        f"- Stage 4 development re-run: "
        f"{stage4_metrics.decision.overall_correct.numerator}/"
        f"{stage4_metrics.decision.overall_correct.denominator}",
        "",
        "## Finding quality on remediation development",
        "",
        f"- Confirmed findings: {quality['confirmed_findings']}",
        f"- Structured, resolving locations: {quality['structured_location']['numerator']}/"
        f"{quality['structured_location']['denominator']}",
        f"- Validated source evidence: {quality['validated_source_evidence']['numerator']}/"
        f"{quality['validated_source_evidence']['denominator']}",
        f"- Validated implementation evidence: "
        f"{quality['validated_implementation_evidence']['numerator']}/"
        f"{quality['validated_implementation_evidence']['denominator']}",
        f"- Recommended corrections: {quality['corrections']['numerator']}/"
        f"{quality['corrections']['denominator']}",
        f"- Validated counterexamples: {quality['valid_counterexamples']['numerator']}/"
        f"{quality['valid_counterexamples']['denominator']}",
        f"- Duplicate findings: {quality['duplicate_findings']}",
        f"- Unsupported findings: {quality['unsupported_findings']}",
        f"- False blockers on expected-PASS cases: "
        f"{quality['false_blockers_on_expected_pass_cases']['numerator']}/"
        f"{quality['false_blockers_on_expected_pass_cases']['denominator']}",
        "",
        "## Approval and conflict handling",
        "",
        f"- Approval case statuses: {approval['summary']['status_correct']['numerator']}/"
        f"{approval['summary']['status_correct']['denominator']}",
        f"- Applied-change outcomes: "
        f"{approval['summary']['applied_changes_correct']['numerator']}/"
        f"{approval['summary']['applied_changes_correct']['denominator']}",
        f"- Conflict-aware outcomes: "
        f"{approval['summary']['conflict_handling_correct']['numerator']}/"
        f"{approval['summary']['conflict_handling_correct']['denominator']}",
        "",
        "## Diagnosis on semantic cases",
        "",
        f"- Primary diagnosis: {semantic['summary']['primary_correct']['numerator']}/"
        f"{semantic['summary']['primary_correct']['denominator']}",
        f"- Specific (non-generic) diagnosis: "
        f"{semantic['summary']['specific_diagnosis']['numerator']}/"
        f"{semantic['summary']['specific_diagnosis']['denominator']}",
        f"- Evidence on both sides: "
        f"{semantic['summary']['evidence_on_both_sides']['numerator']}/"
        f"{semantic['summary']['evidence_on_both_sides']['denominator']}",
        "",
        "## Retained baseline",
        "",
        "The original Stage 4 reports under `reports/final` were not modified. Their "
        "finding-quality figures remain the authoritative pre-remediation baseline. That "
        "report predates the recommendation field, so its correction coverage is recorded "
        "as not measured rather than as zero; the same applies to evidence references, "
        "which it never emitted.",
        "",
    ]
    (OUT / "summary.md").write_text("\n".join(summary), encoding="utf-8")
    print(
        json.dumps(
            {
                "remediation_cases": len(remediation_cases),
                "stage4_dev_cases": len(stage4_cases),
                "remediation_decision": remediation_metrics.decision.overall_correct.model_dump(),
                "finding_quality": quality,
                "approval": approval["summary"],
                "semantic": semantic["summary"],
                "schema_validation": final["schema_validation"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
