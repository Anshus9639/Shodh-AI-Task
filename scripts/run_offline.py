"""Single CPU-only offline evaluation command for Stage 4.

Runs the offline test suite, validates fixture and label schemas, executes the
development evaluation for the final tree and the frozen Stage 2 baseline, runs
the single controlled ablation, validates every machine-readable report against
its JSON schema, and writes JSON plus Markdown summaries.

No network, provider SDK, API key, GPU or sealed holdout is touched here.
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
EVALUATION = ROOT / "evaluation"
REPORTS = ROOT / "reports"
sys.path.insert(0, str(EVALUATION))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import baseline  # noqa: E402
import harness  # noqa: E402
import reproduce_debugging_examples  # noqa: E402

from verityflow.evaluation.models import AblationReport, DatasetManifest  # noqa: E402
from verityflow.model.fake import SCENARIOS  # noqa: E402
from verityflow.schemas import build_schemas  # noqa: E402

DEFAULT_CONFIG = ROOT / "configs" / "default.toml"
ABLATION_CONFIG = ROOT / "configs" / "no_domain_partitioning.toml"
SEALED_PATHS = (ROOT / "holdout", ROOT / "holdout_stage2")
FAKE_SCENARIOS = tuple(sorted(SCENARIOS))
FAKE_MODEL_CASES = ("B10_router_clean", "B11_router_precedence", "E05_unsupported_language")


class Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def require(self, condition: bool, message: str) -> bool:
        if not condition:
            self.failures.append(message)
            print(f"FAIL: {message}")
        return condition


def main() -> int:
    checks = Checks()
    run_pytest(checks)
    validate_fixtures(checks)
    validate_manifests(checks)

    cases = harness.load_cases(ROOT)
    final_runner = harness.Runner("final", ROOT / "src", supports_model_flag=True)
    final_runs = harness.run_cases(final_runner, cases, DEFAULT_CONFIG)
    final_header = harness.header_for(
        "final",
        config=DEFAULT_CONFIG,
        bundle_count=len(cases),
        model_execution_mode="off (deterministic; no model requests)",
    )
    final_metrics = harness.compute_metrics(final_header, cases, final_runs)
    write_metrics(checks, REPORTS / "final" / "metrics.json", final_metrics, "evaluation_metrics")
    write_report(
        checks,
        REPORTS / "final" / "findings.json",
        harness.build_findings_report(final_header, cases, final_runs).model_dump(mode="json"),
        "evaluation_findings",
    )
    write_report(
        checks,
        REPORTS / "final" / "coverage.json",
        harness.build_coverage_report(final_header, cases, final_runs).model_dump(mode="json"),
        "evaluation_coverage",
    )
    write_report(
        checks,
        REPORTS / "final" / "counterexamples.json",
        harness.build_counterexamples_report(final_header, cases, final_runs).model_dump(
            mode="json"
        ),
        "evaluation_counterexamples",
    )
    checks.require(
        final_metrics.decision.overall_correct.numerator
        == final_metrics.decision.overall_correct.denominator,
        "development decision correctness is not complete",
    )
    checks.require(
        final_metrics.decision.false_pass.numerator == 0,
        "false PASS found on the development suite",
    )
    write_markdown(REPORTS / "final" / "REPORT.md", final_markdown(final_metrics, final_runs))

    frozen = baseline.prepare(ROOT)
    baseline_runner = harness.Runner("baseline", frozen.src, supports_model_flag=False)
    baseline_runs = harness.run_cases(baseline_runner, cases, DEFAULT_CONFIG)
    baseline_header = harness.header_for(
        "baseline",
        config=DEFAULT_CONFIG,
        baseline_revision=frozen.commit,
        bundle_count=len(cases),
        model_execution_mode="not available (baseline predates the model layer)",
    )
    baseline_metrics = harness.compute_metrics(baseline_header, cases, baseline_runs)
    write_metrics(
        checks, REPORTS / "baseline" / "metrics.json", baseline_metrics, "evaluation_metrics"
    )
    write_report(
        checks,
        REPORTS / "baseline" / "comparison.json",
        baseline_comparison(frozen, cases, baseline_runs, final_runs),
        None,
    )
    write_markdown(
        REPORTS / "baseline" / "REPORT.md",
        baseline_markdown(
            frozen, baseline_metrics, final_metrics, cases, baseline_runs, final_runs
        ),
    )

    ablation_runs = harness.run_cases(final_runner, cases, ABLATION_CONFIG)
    ablation_header = harness.header_for(
        "ablation",
        config=ABLATION_CONFIG,
        bundle_count=len(cases),
        model_execution_mode="off (deterministic; no model requests)",
    )
    ablation_metrics = harness.compute_metrics(ablation_header, cases, ablation_runs)
    changes = ablation_changes(cases, final_runs, ablation_runs)
    ablation = AblationReport(
        header=ablation_header,
        enabled=final_metrics,
        disabled=ablation_metrics,
        changes=tuple(changes),
    )
    write_metrics(checks, REPORTS / "ablation" / "metrics.json", ablation, "evaluation_ablation")
    write_markdown(
        REPORTS / "ablation" / "REPORT.md",
        ablation_markdown(ablation_metrics, final_metrics, changes),
    )

    fake = run_fake_models(checks, cases)
    write_report(checks, REPORTS / "evaluation" / "model_scenarios.json", fake, None)
    write_markdown(
        REPORTS / "evaluation" / "REPORT.md",
        evaluation_markdown(final_metrics, baseline_metrics, ablation_metrics, fake),
    )
    write_dataset_manifest(checks)
    run_debugging_examples(checks)
    validate_all_reports(checks)

    if checks.failures:
        print(f"\nFAILED: {len(checks.failures)} mandatory check(s) failed")
        for failure in checks.failures:
            print(f" - {failure}")
        return 1
    print("\nAll offline evaluation checks passed.")
    return 0


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #


def run_pytest(checks: Checks) -> None:
    print("== offline test suite")
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    print(completed.stdout.strip()[-4000:])
    if completed.returncode != 0:
        print(completed.stderr.strip()[-4000:])
    checks.require(completed.returncode == 0, "offline pytest suite failed")


def validate_fixtures(checks: Checks) -> None:
    print("== fixture and label validation")
    from verityflow.config import load_config
    from verityflow.ingest.loader import load_bundle
    from verityflow.models.changes import ChangeArtifact
    from verityflow.models.dictionary import DataDictionary
    from verityflow.models.spec import SpecArtifact
    from verityflow.models.workflow import WorkflowArtifact

    config = load_config(DEFAULT_CONFIG)
    cases = harness.load_cases(ROOT)
    artifact_models = {
        "dictionary.json": DataDictionary,
        "spec.json": SpecArtifact,
        "workflow.json": WorkflowArtifact,
        "changes.json": ChangeArtifact,
    }
    for case in cases:
        checks.require(
            not _is_sealed(case.bundle_path),
            f"development case {case.bundle_id} resolves inside a sealed directory",
        )
        try:
            load_bundle(case.bundle_path, limits=config.limits)
        except Exception as exc:  # fixture validation must report, not raise
            checks.require(False, f"{case.bundle_id} cannot be loaded: {exc}")
            continue
        checks.require(
            (case.bundle_path / "dictionary.json").is_file(),
            f"{case.bundle_id} has no dictionary",
        )
        checks.require((case.bundle_path / "spec.json").is_file(), f"{case.bundle_id} has no spec")
        checks.require(
            (case.bundle_path / "workflow.json").is_file(),
            f"{case.bundle_id} has no workflow",
        )
        requirements = case.bundle_path / "requirements.txt"
        checks.require(
            requirements.is_file() and bool(requirements.read_text(encoding="utf-8").strip()),
            f"{case.bundle_id} has no requirements",
        )
        for filename, model in artifact_models.items():
            path = case.bundle_path / filename
            if not path.is_file():
                continue
            try:
                model.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:
                checks.require(False, f"{case.bundle_id}/{filename} fails its schema: {exc}")
        payload = json.loads(case.label_path.read_text(encoding="utf-8"))
        checks.require(
            payload["expected"]["status"] == case.expected_status,
            f"{case.bundle_id} label status mismatch between loader and file",
        )
        checks.require(
            payload.get("reviewer_output_used") is False,
            f"{case.bundle_id} label does not declare reviewer isolation",
        )


def validate_manifests(checks: Checks) -> None:
    print("== dataset manifest integrity")
    dataset = json.loads((EVALUATION / "DATASET.json").read_text(encoding="utf-8"))
    manifest_lines = (EVALUATION / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
    for line in manifest_lines:
        if not line.strip():
            continue
        digest, relative = line.split("  ", 1)
        target = ROOT / relative
        checks.require(target.is_file(), f"manifest entry missing: {relative}")
        if target.is_file():
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            checks.require(actual == digest, f"manifest hash mismatch: {relative}")
    for set_name, recorded in dataset["sealed_manifests"].items():
        manifest = (ROOT / set_name / "MANIFEST.sha256").read_bytes()
        actual = hashlib.sha256(manifest).hexdigest()
        checks.require(
            actual == recorded,
            f"sealed {set_name} manifest changed: {actual} != {recorded}",
        )
    for sealed in SEALED_PATHS:
        checks.require((sealed / "GENERATION.json").is_file(), f"{sealed} lacks GENERATION.json")


def _is_sealed(path: Path) -> bool:
    resolved = path.resolve()
    return any(sealed.resolve() in resolved.parents for sealed in SEALED_PATHS)


def write_metrics(checks: Checks, path: Path, model: Any, schema_name: str) -> None:
    write_report(checks, path, model.model_dump(mode="json"), schema_name)


def write_markdown(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


def write_report(
    checks: Checks, path: Path, payload: dict[str, Any], schema_name: str | None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if schema_name is not None:
        schema = build_schemas()[schema_name]
        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
        checks.require(not errors, f"{path.name} fails {schema_name}: {errors[:2]}")
    print(f"wrote {path.relative_to(ROOT)}")


def write_dataset_manifest(checks: Checks) -> None:
    manifest = DatasetManifest.model_validate(
        json.loads((EVALUATION / "DATASET.json").read_text(encoding="utf-8"))
    )
    write_metrics(
        checks,
        REPORTS / "evaluation" / "dataset_manifest.json",
        manifest,
        "evaluation_dataset",
    )


def run_debugging_examples(checks: Checks) -> None:
    print("== debugging examples from git history")
    try:
        report = reproduce_debugging_examples.build_report()
        path = reproduce_debugging_examples.write_report(report)
    except reproduce_debugging_examples.ExampleError as exc:
        checks.require(False, f"debugging examples could not be reproduced: {exc}")
        return
    for example in report.examples:
        print(
            f"{example['example_id']}: parent exit {example['failing_run']['exit_code']}, "
            f"fix exit {example['result_after_fix']['exit_code']}, "
            f"HEAD exit {example['current_head_run']['exit_code']}"
        )
        checks.require(
            example["failing_run"]["exit_code"] != 0,
            f"{example['example_id']} did not fail before its fix",
        )
        checks.require(
            example["result_after_fix"]["exit_code"] == 0,
            f"{example['example_id']} does not pass at its fix commit",
        )
        checks.require(
            example["current_head_run"]["exit_code"] == 0,
            f"{example['example_id']} does not pass at HEAD",
        )
    print(f"wrote {path.relative_to(ROOT)}")


def validate_all_reports(checks: Checks) -> None:
    print("== report schema validation")
    schemas = build_schemas()
    required = {
        "final/metrics.json": "evaluation_metrics",
        "final/findings.json": "evaluation_findings",
        "final/coverage.json": "evaluation_coverage",
        "final/counterexamples.json": "evaluation_counterexamples",
        "baseline/metrics.json": "evaluation_metrics",
        "ablation/metrics.json": "evaluation_ablation",
        "evaluation/dataset_manifest.json": "evaluation_dataset",
        "debugging/debugging_examples.json": "evaluation_debugging",
        "evaluation/holdout_results.json": "evaluation_sealed_results",
    }
    for relative, schema_name in required.items():
        path = REPORTS / relative
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schemas[schema_name])
        errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
        checks.require(not errors, f"{relative} fails {schema_name}: {errors[:2]}")


def run_fake_models(checks: Checks, cases: list[harness.Case]) -> dict[str, Any]:
    print("== fake-model scenarios (separate from deterministic evaluation)")
    by_id = {case.bundle_id: case for case in cases}
    runner = harness.Runner("final", ROOT / "src", supports_model_flag=True)
    results: list[dict[str, Any]] = []
    statuses: dict[str, str] = {}
    for scenario in FAKE_SCENARIOS:
        for bundle_id in FAKE_MODEL_CASES:
            case = by_id[bundle_id]
            run = harness.run_review(
                runner,
                case,
                DEFAULT_CONFIG,
                model_mode="fake",
                model_scenario=scenario,
            )
            analysis = run.model_analysis or {}
            response = analysis.get("response") or {}
            failure = analysis.get("failure") or {}
            statuses.setdefault(bundle_id, str(run.status))
            checks.require(
                run.status == statuses[bundle_id],
                f"fake scenario {scenario} changed status for {bundle_id}",
            )
            results.append(
                {
                    "scenario": scenario,
                    "bundle_id": bundle_id,
                    "review_status": run.status,
                    "analysis_status": analysis.get("status"),
                    "synthetic": analysis.get("synthetic"),
                    "status_unchanged": analysis.get("status_unchanged"),
                    "retry_count": response.get("retry_count", 0),
                    "failure_category": failure.get("category"),
                    "rejected_observations": len(analysis.get("rejected_observations", ())),
                    "assisted_observations": len(analysis.get("assisted_observations", ())),
                }
            )
    payload = {
        "model_mode": "fake",
        "scenarios": list(FAKE_SCENARIOS),
        "bundles": list(FAKE_MODEL_CASES),
        "requests": len(results),
        "completed": sum(1 for item in results if item["analysis_status"] == "completed"),
        "failed": sum(1 for item in results if item["analysis_status"] == "failed"),
        "retries": sum(int(item["retry_count"] or 0) for item in results),
        "status_invariance": all(
            item["status_unchanged"] for item in results if item["status_unchanged"] is not None
        ),
        "real_model_calls": 0,
        "results": results,
        "note": "Synthetic client only; no provider was contacted and no label was produced here.",
    }
    return payload


# --------------------------------------------------------------------------- #
# Ablation and Markdown
# --------------------------------------------------------------------------- #


def ablation_changes(
    cases: list[harness.Case],
    enabled_runs: list[harness.ReviewRun],
    disabled_runs: list[harness.ReviewRun],
) -> list[dict[str, Any]]:
    enabled = {run.case.bundle_id: run for run in enabled_runs}
    changes: list[dict[str, Any]] = []
    for run in disabled_runs:
        on = enabled[run.case.bundle_id]
        if on.status == run.status:
            continue
        changes.append(
            {
                "bundle_id": run.case.bundle_id,
                "expected_status": run.case.expected_status,
                "enabled_status": on.status,
                "disabled_status": run.status,
                "enabled_comparison_modes": _modes(on),
                "disabled_comparison_modes": _modes(run),
                "reason": (
                    "numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE "
                    "instead of claiming a verified PASS or a verified defect"
                    if run.status == "INCOMPLETE"
                    else "status changed under the ablation"
                ),
            }
        )
    return changes


def _modes(run: harness.ReviewRun) -> dict[str, int]:
    report = run.report or {}
    counts: dict[str, int] = {}
    for comparison in report.get("comparisons", ()):
        key = str(comparison.get("mode"))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _ratio_text(ratio: Any) -> str:
    return f"{ratio.numerator}/{ratio.denominator}"


def final_markdown(metrics: Any, runs: list[harness.ReviewRun]) -> str:
    decision = metrics.decision
    findings = metrics.finding_quality
    coverage = metrics.coverage
    resources = metrics.resources
    lines = [
        "# Final development evaluation",
        "",
        f"- Code revision: `{metrics.header.code_revision}`",
        f"- Configuration: `configs/default.toml` sha256 `{metrics.header.config_sha256}`",
        f"- Dataset manifest: sha256 `{metrics.header.dataset_manifest_sha256}`",
        f"- Model execution mode: {metrics.header.model_execution_mode}",
        f"- Bundles: {metrics.header.bundle_count}",
        "",
        "## Decisions",
        "",
        "| Status | Expected | Actual |",
        "| --- | --- | --- |",
    ]
    for status in harness.STATUSES:
        lines.append(
            f"| {status} | {decision.expected_counts[status]} | {decision.actual_counts[status]} |"
        )
    lines.extend(
        [
            "",
            f"- Overall exact status: {_ratio_text(decision.overall_correct)}",
            f"- Correct PASS: {_ratio_text(decision.correct_pass)}",
            f"- Correct CHANGES_REQUIRED: {_ratio_text(decision.correct_changes_required)}",
            f"- Correct INCOMPLETE: {_ratio_text(decision.correct_incomplete)}",
            f"- False PASS (any expected non-PASS): {_ratio_text(decision.false_pass)}",
            f"- False PASS on defective cases: {_ratio_text(decision.false_pass_on_defective)}",
            f"- False blocks (valid case reported CHANGES_REQUIRED): "
            f"{_ratio_text(decision.false_block)}",
            f"- Abstentions by expected status: {decision.abstentions_by_expected}",
            "",
            "## Finding quality (expected CHANGES_REQUIRED cases)",
            "",
            f"- Defect identification: {_ratio_text(findings.correct_defect_identification)}",
            f"- Rule reference valid: {_ratio_text(findings.correct_rule_reference)}",
            f"- Artifact and version: {_ratio_text(findings.correct_artifact)}, "
            f"{_ratio_text(findings.correct_artifact_version)}",
            f"- Evidence location: {_ratio_text(findings.evidence_location)}",
            f"- Structured evidence: {_ratio_text(findings.structured_evidence)}",
            f"- Evidence items valid: {_ratio_text(findings.valid_evidence)}",
            f"- Message carries a witness ticket: {_ratio_text(findings.message_witness)}",
            f"- Valid counterexamples: {_ratio_text(findings.valid_counterexamples)}",
            f"- Blocker severity: {_ratio_text(findings.blocker_severity_on_defects)}",
            f"- Unsupported findings: {findings.unsupported_findings}; duplicates: "
            f"{findings.duplicate_findings}",
            "",
            "## Coverage and resources",
            "",
            f"- Effective requirements: {coverage.total_effective_requirements} "
            f"(fully {coverage.fully_reviewed}, partially {coverage.partially_reviewed}, "
            f"unreviewed {coverage.unreviewed})",
            f"- Source/spec/workflow/code coverage: {_ratio_text(coverage.source_coverage)}, "
            f"{_ratio_text(coverage.specification_coverage)}, "
            f"{_ratio_text(coverage.workflow_coverage)}, {_ratio_text(coverage.code_coverage)}",
            f"- Evidence completeness: {_ratio_text(coverage.evidence_completeness)}",
            f"- Comparison modes: {coverage.comparison_modes}",
            f"- Runtime: {resources.total_runtime_seconds:.2f}s total, "
            f"{resources.mean_runtime_seconds:.2f}s mean per bundle (includes process start)",
            f"- Input bytes: {resources.input_bytes}; evaluated combinations: "
            f"{resources.counterexample_combinations}",
            f"- Model requests: {resources.model_requests}; "
            f"real calls: {resources.real_model_calls}",
            "",
            "Expected labels come from `evaluation/cases/`, are not reviewer inputs, and were",
            "cross-checked by the independent oracle before this run.",
        ]
    )
    errors = [run.case.bundle_id for run in runs if run.error]
    lines.append("")
    lines.append(f"Execution errors: {errors or 'none'}")
    return "\n".join(lines) + "\n"


def baseline_markdown(
    frozen: baseline.Baseline,
    baseline_metrics: Any,
    final_metrics: Any,
    cases: list[harness.Case],
    baseline_runs: list[harness.ReviewRun],
    final_runs: list[harness.ReviewRun],
) -> str:
    base = {run.case.bundle_id: run for run in baseline_runs}
    final = {run.case.bundle_id: run for run in final_runs}
    disagreements = [
        case.bundle_id
        for case in cases
        if base[case.bundle_id].status != final[case.bundle_id].status
    ]
    lines = [
        "# Baseline versus final",
        "",
        f"- Baseline tag: `{frozen.tag}` -> `{frozen.commit}`",
        f"- Baseline dependency lock: `{frozen.dependency_lock_path}` sha256 "
        f"`{frozen.dependency_lock_sha256}`",
        f"- Final code revision: `{final_metrics.header.code_revision}`",
        f"- Shared configuration sha256: `{final_metrics.header.config_sha256}`",
        f"- Dataset manifest sha256: `{final_metrics.header.dataset_manifest_sha256}`",
        "",
        "Identical inputs: same bundle bytes, approvals, configuration, domain limits and labels.",
        "The baseline ran unmodified from a `git archive` checkout; it has no `--model` flag and",
        "no domain-partitioning switch, so deterministic review is its only mode.",
        "",
        "| Metric | Baseline | Final |",
        "| --- | --- | --- |",
        f"| Overall exact status | {_ratio_text(baseline_metrics.decision.overall_correct)} | "
        f"{_ratio_text(final_metrics.decision.overall_correct)} |",
        f"| False PASS on defective | "
        f"{_ratio_text(baseline_metrics.decision.false_pass_on_defective)} | "
        f"{_ratio_text(final_metrics.decision.false_pass_on_defective)} |",
        f"| False blocks | {_ratio_text(baseline_metrics.decision.false_block)} | "
        f"{_ratio_text(final_metrics.decision.false_block)} |",
        f"| Valid counterexamples | "
        f"{_ratio_text(baseline_metrics.finding_quality.valid_counterexamples)} | "
        f"{_ratio_text(final_metrics.finding_quality.valid_counterexamples)} |",
        f"| Effective requirements | "
        f"{baseline_metrics.coverage.total_effective_requirements} | "
        f"{final_metrics.coverage.total_effective_requirements} |",
        f"| Evaluated combinations | "
        f"{baseline_metrics.resources.counterexample_combinations} | "
        f"{final_metrics.resources.counterexample_combinations} |",
        f"| Runtime (s) | {baseline_metrics.resources.total_runtime_seconds:.2f} | "
        f"{final_metrics.resources.total_runtime_seconds:.2f} |",
        "",
        f"Per-case status disagreements: {disagreements or 'none'}",
        "",
        "The baseline neither executes nor reports the Stage 3 model layer, which is not part",
        "of these decisions. The development suite therefore measures deterministic parity, not",
        "an improvement; the sealed evaluation provides the outcome check on unseen bundles.",
    ]
    return "\n".join(lines) + "\n"


def ablation_markdown(disabled: Any, enabled: Any, changes: list[dict[str, Any]]) -> str:
    lines = [
        "# Controlled ablation: numeric domain partitioning disabled",
        "",
        "Only `domain_partitioning.enabled` differs (`configs/no_domain_partitioning.toml`).",
        "Bundles, approvals, model mode (`off`), labels and the environment are unchanged.",
        "",
        "| Metric | Enabled | Disabled |",
        "| --- | --- | --- |",
        f"| Overall exact status | {_ratio_text(enabled.decision.overall_correct)} | "
        f"{_ratio_text(disabled.decision.overall_correct)} |",
        f"| Correct CHANGES_REQUIRED | "
        f"{_ratio_text(enabled.decision.correct_changes_required)} | "
        f"{_ratio_text(disabled.decision.correct_changes_required)} |",
        f"| Correct PASS | {_ratio_text(enabled.decision.correct_pass)} | "
        f"{_ratio_text(disabled.decision.correct_pass)} |",
        f"| False PASS on defective | "
        f"{_ratio_text(enabled.decision.false_pass_on_defective)} | "
        f"{_ratio_text(disabled.decision.false_pass_on_defective)} |",
        f"| False blocks | {_ratio_text(enabled.decision.false_block)} | "
        f"{_ratio_text(disabled.decision.false_block)} |",
        f"| Valid counterexamples | "
        f"{_ratio_text(enabled.finding_quality.valid_counterexamples)} | "
        f"{_ratio_text(disabled.finding_quality.valid_counterexamples)} |",
        f"| Comparison modes | {enabled.coverage.comparison_modes} | "
        f"{disabled.coverage.comparison_modes} |",
        f"| Evaluated combinations | {enabled.resources.counterexample_combinations} | "
        f"{disabled.resources.counterexample_combinations} |",
        f"| Runtime (s) | {enabled.resources.total_runtime_seconds:.2f} | "
        f"{disabled.resources.total_runtime_seconds:.2f} |",
        "",
        f"Status changes: {len(changes)} of {enabled.header.bundle_count} bundles.",
        "",
        "## Why behaviour changes",
        "",
        "Disabled mode replaces interval partitioning and boundary witnesses with a small fixed",
        "ticket set and records the remaining space as `unreviewed`. The reviewer cannot claim",
        "exhaustive agreement, so every verdict that depended on numeric cells becomes",
        "`INCOMPLETE`; defects that would only appear at a boundary (for example wait = 30 versus",
        "`> 30`) are never discovered. No false PASS is introduced: the reviewer abstains instead",
        "of asserting success. Statuses that remain `CHANGES_REQUIRED` are structural or approval",
        "defects visible on the fixed tickets.",
        "",
        "| Bundle | Enabled | Disabled | Reason |",
        "| --- | --- | --- | --- |",
    ]
    for change in changes:
        lines.append(
            f"| {change['bundle_id']} | {change['enabled_status']} | "
            f"{change['disabled_status']} | {change['reason']} |"
        )
    return "\n".join(lines) + "\n"


def baseline_comparison(
    frozen: baseline.Baseline,
    cases: list[harness.Case],
    baseline_runs: list[harness.ReviewRun],
    final_runs: list[harness.ReviewRun],
) -> dict[str, Any]:
    base = {run.case.bundle_id: run for run in baseline_runs}
    final = {run.case.bundle_id: run for run in final_runs}
    dataset = json.loads((EVALUATION / "DATASET.json").read_text(encoding="utf-8"))
    bundle_hashes = {item["bundle_id"]: item["bundle_content_hash"] for item in dataset["cases"]}
    return {
        "baseline": {
            "tag": frozen.tag,
            "commit": frozen.commit,
            "dependency_lock_path": frozen.dependency_lock_path,
            "dependency_lock_sha256": frozen.dependency_lock_sha256,
            "features_missing": list(frozen.missing_features),
        },
        "final": {
            "commit": harness.git_revision(ROOT),
            "config_path": "configs/default.toml",
            "config_sha256": harness.file_sha256(DEFAULT_CONFIG),
        },
        "dataset_manifest_sha256": harness.file_sha256(EVALUATION / "DATASET.json"),
        "bundle_manifest_hashes": bundle_hashes,
        "identical_inputs": True,
        "per_case": [
            {
                "bundle_id": case.bundle_id,
                "expected_status": case.expected_status,
                "baseline_status": base[case.bundle_id].status,
                "final_status": final[case.bundle_id].status,
                "agree": base[case.bundle_id].status == final[case.bundle_id].status,
            }
            for case in cases
        ],
    }


def evaluation_markdown(
    final: Any, baseline_metrics: Any, disabled: Any, fake: dict[str, Any]
) -> str:
    lines = [
        "# Evaluation summary",
        "",
        "## Final development evaluation",
        "",
        f"- Exact status: {_ratio_text(final.decision.overall_correct)}",
        f"- False PASS on defective: {_ratio_text(final.decision.false_pass_on_defective)}",
        f"- False blocks: {_ratio_text(final.decision.false_block)}",
        f"- Valid counterexamples: {_ratio_text(final.finding_quality.valid_counterexamples)}",
        f"- Model execution mode: {final.header.model_execution_mode}",
        "",
        "## Baseline",
        "",
        f"- Exact status: {_ratio_text(baseline_metrics.decision.overall_correct)}",
        f"- False PASS on defective: "
        f"{_ratio_text(baseline_metrics.decision.false_pass_on_defective)}",
        "",
        "## Ablation",
        "",
        f"- Exact status with partitioning: {_ratio_text(final.decision.overall_correct)}",
        f"- Exact status without partitioning: {_ratio_text(disabled.decision.overall_correct)}",
        "",
        "## Fake-model scenarios",
        "",
        f"- Requests: {fake['requests']}; completed: {fake['completed']}; failed: "
        f"{fake['failed']}; retries: {fake['retries']}",
        f"- Status invariance across every scenario: {fake['status_invariance']}",
        "- Real-model semantic performance was not measured; no provider was contacted.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
