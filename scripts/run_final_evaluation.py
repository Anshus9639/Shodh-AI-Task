"""The single controlled sealed evaluation run.

Executes the two frozen holdouts exactly once, only after the implementation
tree is committed and clean. Labels live outside the bundles and are read here,
after each reviewer process has finished, never as reviewer input. Results are
written to ``reports/evaluation/holdout_results.json`` and retained.

Run this once, then stop and review. Do not tune the reviewer against these
outcomes.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evaluation"))
sys.path.insert(0, str(ROOT / "src"))

import harness  # noqa: E402

from verityflow.evaluation.models import SealedResults  # noqa: E402
from verityflow.schemas import build_schemas  # noqa: E402

DEFAULT_CONFIG = ROOT / "configs" / "default.toml"
REPORTS = ROOT / "reports"

# Stage 1 labels grade parse/mapping/resolution/findings/routes and carry no
# status. These statuses follow the documented Stage 1 expectation table and
# the status rules: INFO findings do not block, COVERAGE_GAP findings abstain.
STAGE1_STATUS = {
    "F01_clean": "PASS",
    "F02_renamed": "PASS",
    "F03_approved_threshold": "PASS",
    "F04_unapproved_threshold": "PASS",
    "F05_tampered_approval": "INCOMPLETE",
}
STAGE1_METHOD = (
    "manual derivation from the frozen label categories and docs/evaluation_protocol.md: "
    "UNAPPROVED_CHANGE is INFO (PASS), INVALID_APPROVAL is COVERAGE_GAP (INCOMPLETE)"
)


def main() -> int:
    dirty = changed_implementation_paths()
    if dirty:
        print("refusing to run the sealed evaluation from an uncommitted implementation:")
        for path in dirty:
            print(f" - {path}")
        return 2
    manifest_before = sealed_manifest_hashes()
    cases = sealed_cases()
    runner = harness.Runner("final", ROOT / "src", supports_model_flag=True)
    results: list[dict[str, Any]] = []
    for case in cases:
        run = harness.run_review(runner, case, DEFAULT_CONFIG)
        results.append(assess(case, run))
    expected_counts = {"PASS": 0, "CHANGES_REQUIRED": 0, "INCOMPLETE": 0}
    actual_counts = {"PASS": 0, "CHANGES_REQUIRED": 0, "INCOMPLETE": 0}
    for item in results:
        expected_counts[item["expected_status"]] += 1
        if item["actual_status"] in actual_counts:
            actual_counts[item["actual_status"]] += 1
    manifest_after = sealed_manifest_hashes()
    header = harness.header_for(
        "sealed_final",
        config=DEFAULT_CONFIG,
        bundle_count=len(cases),
        model_execution_mode="off (deterministic; no model requests)",
    )
    report = SealedResults(
        header=header,
        expected_outcome_counts=expected_counts,
        actual_outcome_counts=actual_counts,
        cases=tuple(results),
    )
    output = REPORTS / "evaluation" / "holdout_results.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    schema = build_schemas()["evaluation_sealed_results"]
    errors = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(json.loads(output.read_text())),
        key=lambda error: list(error.path),
    )
    write_markdown(results, expected_counts, actual_counts, manifest_before, manifest_after)
    correct = sum(1 for item in results if item["correct"])
    print(f"sealed decisions correct: {correct}/{len(results)}")
    print(f"expected {expected_counts}; actual {actual_counts}")
    if errors:
        print(f"holdout_results.json schema errors: {errors[:3]}")
        return 1
    integrity = manifest_before == manifest_after
    print(f"sealed manifests unchanged: {integrity}")
    return 0 if correct == len(results) and integrity else 1


def changed_implementation_paths() -> list[str]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "status",
            "--porcelain",
            "--",
            "src",
            "scripts",
            "configs",
            "pyproject.toml",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return [
        line[3:]
        for line in completed.stdout.splitlines()
        if line.strip() and not line[3:].startswith(("reports/", "docs/"))
    ]


def sealed_cases() -> list[harness.Case]:
    cases: list[harness.Case] = []
    stage1 = ROOT / "holdout"
    for name, status in STAGE1_STATUS.items():
        label_path = stage1 / "labels" / f"{name}.json"
        label = json.loads(label_path.read_text(encoding="utf-8"))
        expected = label["expected"]
        cases.append(
            harness.Case(
                bundle_id=name,
                bundle_path=stage1 / "bundles" / name,
                expected_status=status,
                defect_classes=tuple(expected.get("finding_categories", ())),
                primary_defect=None,
                scenarios=("sealed stage 1",),
                expected_tickets=dict(expected.get("routes", {})),
                label_path=label_path,
            )
        )
    stage2 = ROOT / "holdout_stage2"
    for label_path in sorted((stage2 / "labels").glob("*.json")):
        label = json.loads(label_path.read_text(encoding="utf-8"))
        expected = label["expected"]
        cases.append(
            harness.Case(
                bundle_id=label["bundle"],
                bundle_path=stage2 / "bundles" / label["bundle"],
                expected_status=expected["status"],
                defect_classes=tuple(expected.get("finding_categories", ())),
                primary_defect=None,
                scenarios=("sealed stage 2",),
                expected_tickets=dict(expected.get("routes", {})),
                label_path=label_path,
            )
        )
    return cases


def assess(case: harness.Case, run: harness.ReviewRun) -> dict[str, Any]:
    report = run.report or {}
    categories = [finding.get("category") for finding in report.get("findings", ())]
    category_set = set(categories)
    expected_counterexamples = _expected_counterexamples(case)
    counterexample_checks = [
        _counterexample_found(report, item) for item in expected_counterexamples
    ]
    checks = {
        "status": run.status == case.expected_status,
        "finding_categories": category_set == set(case.defect_classes),
        "counterexamples": all(counterexample_checks),
    }
    return {
        "bundle_id": case.bundle_id,
        "suite": case.scenarios[0],
        "bundle_path": str(case.bundle_path.relative_to(ROOT)),
        "label_path": str(case.label_path.relative_to(ROOT)),
        "expected_status": case.expected_status,
        "actual_status": run.status or "ERROR",
        "expected_finding_categories": list(case.defect_classes),
        "actual_finding_categories": categories,
        "expected_counterexamples": expected_counterexamples,
        "counterexample_checks": counterexample_checks,
        "checks": checks,
        "correct": bool(run.status == case.expected_status and run.error is None),
        "method": STAGE1_METHOD if case.bundle_id.startswith("F") else "frozen Stage 2 label",
        "runtime_seconds": round(run.runtime_seconds, 3),
        "input_bytes": run.input_bytes,
        "error": run.error,
    }


def _expected_counterexamples(case: harness.Case) -> list[dict[str, Any]]:
    if not case.bundle_id.startswith("G"):
        return []
    label = json.loads(case.label_path.read_text(encoding="utf-8"))
    return list(label["expected"].get("counterexamples", ()))


def _counterexample_found(report: dict[str, Any], expected: dict[str, Any]) -> bool:
    ticket = expected.get("ticket", {})
    for comparison in report.get("comparisons", ()):
        for counterexample in comparison.get("counterexamples", ()):
            inputs = counterexample.get("inputs", {})
            if not all(inputs.get(key) == value for key, value in ticket.items()):
                continue
            if counterexample.get("requirement_outcome") != expected.get("expected_destination"):
                continue
            if counterexample.get("artifact_outcome") != expected.get("actual_destination"):
                continue
            if counterexample.get("validated"):
                return True
    return False


def sealed_manifest_hashes() -> dict[str, str]:
    return {
        set_id: hashlib.sha256((ROOT / set_id / "MANIFEST.sha256").read_bytes()).hexdigest()
        for set_id in ("holdout", "holdout_stage2")
    }


def write_markdown(
    results: list[dict[str, Any]],
    expected_counts: dict[str, int],
    actual_counts: dict[str, int],
    manifest_before: dict[str, str],
    manifest_after: dict[str, str],
) -> None:
    lines = [
        "# Sealed final evaluation",
        "",
        f"- Expected: {expected_counts}",
        f"- Actual: {actual_counts}",
        f"- Correct statuses: {sum(1 for item in results if item['correct'])}/{len(results)}",
        f"- Sealed manifests unchanged: {manifest_before == manifest_after}",
        f"- Manifest hashes: `{manifest_before}`",
        "",
        "| Bundle | Expected | Actual | Expected findings | Actual findings | Categories match |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        lines.append(
            f"| {item['bundle_id']} | {item['expected_status']} | {item['actual_status']} | "
            f"{', '.join(item['expected_finding_categories']) or '-'} | "
            f"{', '.join(item['actual_finding_categories']) or '-'} | "
            f"{item['checks']['finding_categories']} |"
        )
    lines.extend(
        [
            "",
            "Counterexample checks apply to the frozen Stage 2 defect labels (G03, G04).",
            "Route expectations for valid bundles are not separately graded here; the",
            "behavioural equality comparisons and status cover them, and this scope",
            "limitation is recorded in the engineering note.",
        ]
    )
    path = REPORTS / "evaluation" / "HOLDOUT_REPORT.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
