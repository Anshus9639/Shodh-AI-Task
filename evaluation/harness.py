"""Run the reviewer over labelled bundles and aggregate Stage 4 metrics.

The reviewer always executes in a fresh subprocess with an explicit
``PYTHONPATH``. Expected labels are read and applied here, after the process
has produced its report; they are never written into a bundle or passed to the
reviewer. Every number in the reports is computed from executed outputs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from verityflow.evaluation.models import (
    CaseRecord,
    CounterexamplesReport,
    CoverageMetrics,
    CoverageReport,
    DecisionMetrics,
    EvaluationMetrics,
    FindingMetrics,
    FindingsReport,
    Ratio,
    ReportHeader,
    ResourceMetrics,
)

STATUSES = ("PASS", "CHANGES_REQUIRED", "INCOMPLETE")
DEFECTIVE = "CHANGES_REQUIRED"
UNSUPPORTED_CATEGORIES = frozenset({"UNSUPPORTED_LANGUAGE", "UNSUPPORTED_CODE"})
_WITNESS_PATTERN = re.compile(r"(category|priority|wait_time)=")

REPO_ROOT = Path(__file__).resolve().parent.parent


class HarnessError(RuntimeError):
    """A case cannot be executed or a mandatory input is missing."""


@dataclass(frozen=True)
class Case:
    bundle_id: str
    bundle_path: Path
    expected_status: str
    defect_classes: tuple[str, ...]
    primary_defect: str | None
    scenarios: tuple[str, ...]
    expected_tickets: dict[str, str]
    label_path: Path


@dataclass(frozen=True)
class ReviewRun:
    case: Case
    report: dict[str, Any] | None
    model_analysis: dict[str, Any] | None
    exit_code: int
    runtime_seconds: float
    input_bytes: int
    error: str | None

    @property
    def status(self) -> str | None:
        if self.report is None:
            return None
        return str(self.report.get("status"))


@dataclass(frozen=True)
class Runner:
    revision: str
    src_root: Path
    supports_model_flag: bool

    def command(
        self,
        bundle: Path,
        config: Path,
        model_mode: str,
        model_scenario: str | None,
    ) -> list[str]:
        command = [
            sys.executable,
            "-c",
            "from verityflow.cli import app; app()",
            "review",
            str(bundle),
            "--config",
            str(config),
        ]
        if self.supports_model_flag:
            command.extend(["--model", model_mode])
            if model_scenario is not None:
                command.extend(["--model-scenario", model_scenario])
        return command


def load_cases(repo: Path = REPO_ROOT, case_dir: Path | None = None) -> list[Case]:
    directory = case_dir or repo / "evaluation" / "cases"
    if not directory.is_dir():
        raise HarnessError(f"case label directory {directory} does not exist")
    cases: list[Case] = []
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = payload["expected"]
        cases.append(
            Case(
                bundle_id=payload["bundle_id"],
                bundle_path=repo / payload["bundle_path"],
                expected_status=expected["status"],
                defect_classes=tuple(expected.get("defect_classes", ())),
                primary_defect=expected.get("primary_defect"),
                scenarios=tuple(payload.get("scenarios", ())),
                expected_tickets=dict(expected.get("tickets", {})),
                label_path=path,
            )
        )
    if not cases:
        raise HarnessError(f"no case labels found in {directory}")
    return cases


def run_review(
    runner: Runner,
    case: Case,
    config: Path,
    *,
    model_mode: str = "off",
    model_scenario: str | None = None,
    timeout: float = 180.0,
) -> ReviewRun:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(runner.src_root)
    environment["PYTHONHASHSEED"] = "0"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    input_bytes = _input_bytes(case.bundle_path)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            runner.command(case.bundle_path, config, model_mode, model_scenario),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(REPO_ROOT),
            env=environment,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ReviewRun(
            case=case,
            report=None,
            model_analysis=None,
            exit_code=-1,
            runtime_seconds=time.perf_counter() - started,
            input_bytes=input_bytes,
            error=f"timeout after {timeout:.0f}s",
        )
    runtime = time.perf_counter() - started
    report: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None
    error: str | None = None
    payload: Any = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            error = f"reviewer stdout is not JSON: {exc}"
    if isinstance(payload, dict) and "review" in payload:
        report = payload.get("review")
        analysis = payload.get("model_analysis")
    elif isinstance(payload, dict):
        report = payload
    if completed.returncode not in (0, 1):
        error = f"exit {completed.returncode}: {completed.stderr.strip()[-2000:]}"
    elif report is None and error is None:
        error = f"no report emitted; stderr: {completed.stderr.strip()[-2000:]}"
    return ReviewRun(
        case=case,
        report=report,
        model_analysis=analysis,
        exit_code=completed.returncode,
        runtime_seconds=runtime,
        input_bytes=input_bytes,
        error=error,
    )


def run_cases(
    runner: Runner,
    cases: list[Case],
    config: Path,
    *,
    model_mode: str = "off",
    model_scenario: str | None = None,
) -> list[ReviewRun]:
    return [
        run_review(
            runner,
            case,
            config,
            model_mode=model_mode,
            model_scenario=model_scenario,
        )
        for case in cases
    ]


def header_for(
    report: str,
    *,
    repo: Path = REPO_ROOT,
    config: Path | None = None,
    baseline_revision: str | None = None,
    bundle_count: int = 0,
    model_execution_mode: str = "off",
) -> ReportHeader:
    config_path = config or repo / "configs" / "default.toml"
    manifest_path = repo / "evaluation" / "DATASET.json"
    return ReportHeader(
        report=report,
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        code_revision=git_revision(repo),
        baseline_revision=baseline_revision,
        config_sha256=file_sha256(config_path),
        dataset_manifest_sha256=file_sha256(manifest_path),
        bundle_count=bundle_count,
        model_execution_mode=model_execution_mode,
        unsupported_scope=(
            "real-model semantic performance is not measured by this report",
            "sealed holdout labels are excluded from reviewer input",
        ),
    )


def compute_metrics(
    header: ReportHeader,
    cases: list[Case],
    runs: list[ReviewRun],
) -> EvaluationMetrics:
    pairs = _pairs(cases, runs)
    decision = _decision_metrics(pairs)
    findings = _finding_metrics(pairs)
    coverage = _coverage_metrics(pairs)
    resources = ResourceMetrics(
        total_runtime_seconds=round(sum(run.runtime_seconds for _, run in pairs), 3),
        mean_runtime_seconds=round(sum(run.runtime_seconds for _, run in pairs) / len(pairs), 3),
        bundle_count=len(pairs),
        input_bytes=sum(run.input_bytes for _, run in pairs),
        counterexample_combinations=sum(_cells_checked(run) for _, run in pairs),
        model_requests=sum(_analysis_requests(run) for _, run in pairs),
        model_successes=sum(_analysis_successes(run) for _, run in pairs),
        model_failures=sum(_analysis_failures(run) for _, run in pairs),
        model_retries=sum(_analysis_retries(run) for _, run in pairs),
        fake_client_calls=sum(_fake_calls(run) for _, run in pairs),
        real_model_calls=0,
    )
    records = tuple(_case_record(case, run) for case, run in pairs)
    return EvaluationMetrics(
        header=header,
        decision=decision,
        finding_quality=findings,
        coverage=coverage,
        resources=resources,
        cases=records,
    )


def build_findings_report(
    header: ReportHeader, cases: list[Case], runs: list[ReviewRun]
) -> FindingsReport:
    payload: list[dict[str, Any]] = []
    for case, run in _pairs(cases, runs):
        report = run.report or {}
        artifact_versions = _artifact_versions(case.bundle_path)
        effective_rules = {
            entry.get("rule_ref") for entry in report.get("coverage", ()) if entry.get("rule_ref")
        }
        findings: list[dict[str, Any]] = []
        for finding in report.get("findings", ()):
            category = finding.get("category")
            artifact_id = finding.get("artifact_id")
            findings.append(
                {
                    "finding_id": finding.get("finding_id"),
                    "severity": finding.get("severity"),
                    "category": category,
                    "rule_ref": finding.get("rule_ref"),
                    "artifact_id": artifact_id,
                    "artifact_version": finding.get("artifact_version"),
                    "message": finding.get("message"),
                    "location": finding.get("location"),
                    "evidence_count": len(finding.get("evidence", ())),
                    "assessment": {
                        "matches_expected_class": category in case.defect_classes,
                        "rule_reference_valid": finding.get("rule_ref") in effective_rules,
                        "artifact_exists": artifact_id in artifact_versions,
                        "artifact_version_matches": artifact_versions.get(artifact_id)
                        == finding.get("artifact_version"),
                        "location_resolves": _location_resolves(
                            finding.get("location"), case.bundle_path
                        ),
                        "has_structured_evidence": bool(finding.get("evidence")),
                        "evidence_items_valid": sum(
                            _location_resolves(item.get("location"), case.bundle_path)
                            for item in finding.get("evidence", ())
                        ),
                        "message_has_witness": bool(
                            _WITNESS_PATTERN.search(str(finding.get("message", "")))
                        ),
                        "severity_is_blocker": finding.get("severity") == "BLOCKER",
                    },
                }
            )
        counterexamples = [
            counterexample
            for comparison in report.get("comparisons", ())
            for counterexample in comparison.get("counterexamples", ())
        ]
        payload.append(
            {
                "bundle_id": case.bundle_id,
                "expected_status": case.expected_status,
                "actual_status": run.status,
                "expected_defect_classes": list(case.defect_classes),
                "findings": findings,
                "counterexamples_total": len(counterexamples),
                "counterexamples_valid": sum(
                    _counterexample_valid(item) for item in counterexamples
                ),
                "error": run.error,
            }
        )
    return FindingsReport(header=header, cases=tuple(payload))


def build_coverage_report(
    header: ReportHeader, cases: list[Case], runs: list[ReviewRun]
) -> CoverageReport:
    payload: list[dict[str, Any]] = []
    for case, run in _pairs(cases, runs):
        report = run.report or {}
        entries = list(report.get("coverage", ()))
        payload.append(
            {
                "bundle_id": case.bundle_id,
                "actual_status": run.status,
                "exhaustive": report.get("exhaustive"),
                "effective_requirements": len(entries),
                "fully_reviewed": sum(_fully_reviewed(entry) for entry in entries),
                "partially_reviewed": sum(_partially_reviewed(entry) for entry in entries),
                "unreviewed": sum(not entry.get("covered") for entry in entries),
                "missing_spec_element": sum(not entry.get("spec_elements") for entry in entries),
                "missing_workflow_element": sum(
                    not entry.get("workflow_elements") for entry in entries
                ),
                "missing_code_witness": sum(
                    entry.get("code_artifact") is None
                    for entry in entries
                    if _bundle_links_code(case.bundle_path)
                ),
                "entries": entries,
                "unreviewed_scopes": list(report.get("unreviewed", ())),
                "comparison_modes": dict(
                    Counter(comparison.get("mode") for comparison in report.get("comparisons", ()))
                ),
            }
        )
    return CoverageReport(header=header, cases=tuple(payload))


def build_counterexamples_report(
    header: ReportHeader, cases: list[Case], runs: list[ReviewRun]
) -> CounterexamplesReport:
    payload: list[dict[str, Any]] = []
    for case, run in _pairs(cases, runs):
        report = run.report or {}
        comparisons = [
            {
                "name": comparison.get("name"),
                "artifact_ref": comparison.get("artifact_ref"),
                "mode": comparison.get("mode"),
                "equal": comparison.get("equal"),
                "cells_checked": comparison.get("cells_checked", 0),
                "combinations_total": comparison.get("combinations_total", 0),
                "combinations_skipped": comparison.get("combinations_skipped", 0),
                "detail": comparison.get("detail"),
                "counterexamples": [
                    {
                        **counterexample,
                        "outputs_differ": counterexample.get("requirement_outcome")
                        != counterexample.get("artifact_outcome"),
                        "valid": _counterexample_valid(counterexample),
                    }
                    for counterexample in comparison.get("counterexamples", ())
                ],
            }
            for comparison in report.get("comparisons", ())
            if comparison.get("counterexamples")
        ]
        payload.append(
            {
                "bundle_id": case.bundle_id,
                "expected_status": case.expected_status,
                "actual_status": run.status,
                "comparisons": comparisons,
            }
        )
    return CounterexamplesReport(header=header, cases=tuple(payload))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def git_revision(repo: Path = REPO_ROOT) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip() or "unknown"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# Metric helpers
# --------------------------------------------------------------------------- #


def _pairs(cases: list[Case], runs: list[ReviewRun]) -> list[tuple[Case, ReviewRun]]:
    by_id = {run.case.bundle_id: run for run in runs}
    missing = [case.bundle_id for case in cases if case.bundle_id not in by_id]
    if missing:
        raise HarnessError(f"missing runs for cases: {', '.join(missing)}")
    return [(case, by_id[case.bundle_id]) for case in cases]


def _ratio(numerator: int, denominator: int) -> Ratio:
    return Ratio(numerator=numerator, denominator=denominator)


def _decision_metrics(pairs: list[tuple[Case, ReviewRun]]) -> DecisionMetrics:
    expected = Counter(case.expected_status for case, _ in pairs)
    actual = Counter(run.status for _, run in pairs if run.status is not None)
    expected_counts = {status: expected.get(status, 0) for status in STATUSES}
    actual_counts = {status: actual.get(status, 0) for status in STATUSES}
    total = len(pairs)
    correct = [run.status == case.expected_status for case, run in pairs]
    non_pass_expected = sum(1 for case, _ in pairs if case.expected_status != "PASS")
    false_pass = sum(
        1 for case, run in pairs if case.expected_status != "PASS" and run.status == "PASS"
    )
    false_pass_defective = sum(
        1 for case, run in pairs if case.expected_status == DEFECTIVE and run.status == "PASS"
    )
    return DecisionMetrics(
        total_cases=total,
        expected_counts=expected_counts,
        actual_counts=actual_counts,
        correct_pass=_ratio(
            sum(
                1 for case, run in pairs if case.expected_status == "PASS" and run.status == "PASS"
            ),
            expected_counts["PASS"],
        ),
        false_pass=_ratio(false_pass, non_pass_expected),
        false_pass_on_defective=_ratio(false_pass_defective, expected_counts[DEFECTIVE]),
        correct_changes_required=_ratio(
            sum(
                1
                for case, run in pairs
                if case.expected_status == DEFECTIVE and run.status == DEFECTIVE
            ),
            expected_counts[DEFECTIVE],
        ),
        false_block=_ratio(
            sum(
                1
                for case, run in pairs
                if case.expected_status == "PASS" and run.status == DEFECTIVE
            ),
            expected_counts["PASS"],
        ),
        correct_incomplete=_ratio(
            sum(
                1
                for case, run in pairs
                if case.expected_status == "INCOMPLETE" and run.status == "INCOMPLETE"
            ),
            expected_counts["INCOMPLETE"],
        ),
        abstentions_by_expected={
            status: sum(
                1
                for case, run in pairs
                if case.expected_status == status and run.status == "INCOMPLETE"
            )
            for status in STATUSES
        },
        overall_correct=_ratio(sum(correct), total),
    )


def _finding_metrics(pairs: list[tuple[Case, ReviewRun]]) -> FindingMetrics:
    defective = [(case, run) for case, run in pairs if case.expected_status == DEFECTIVE]
    findings_total = 0
    identification = 0
    cited_rules = 0
    cited_artifacts = 0
    cited_versions = 0
    located = 0
    structured = 0
    evidence_items = 0
    valid_evidence = 0
    witnesses = 0
    blockers = 0
    identifying_total = 0
    unsupported = 0
    duplicates = 0
    for case, run in pairs:
        report = run.report or {}
        findings = list(report.get("findings", ()))
        findings_total += len(findings)
        unsupported += sum(1 for f in findings if f.get("category") in UNSUPPORTED_CATEGORIES)
        if case.expected_status != DEFECTIVE:
            continue
        artifact_versions = _artifact_versions(case.bundle_path)
        effective_rules = {
            entry.get("rule_ref") for entry in report.get("coverage", ()) if entry.get("rule_ref")
        }
        identifying = [f for f in findings if f.get("category") in case.defect_classes]
        if identifying:
            identification += 1
        identifying_total += len(identifying)
        for finding in identifying:
            cited_rules += finding.get("rule_ref") in effective_rules
            artifact_id = finding.get("artifact_id")
            cited_artifacts += artifact_id in artifact_versions
            cited_versions += artifact_versions.get(artifact_id) == finding.get("artifact_version")
            located += _location_resolves(finding.get("location"), case.bundle_path)
            structured += bool(finding.get("evidence"))
            for item in finding.get("evidence", ()):
                evidence_items += 1
                valid_evidence += _location_resolves(item.get("location"), case.bundle_path)
            witnesses += bool(_WITNESS_PATTERN.search(str(finding.get("message", ""))))
            blockers += finding.get("severity") == "BLOCKER"
        keys = Counter(
            (
                f.get("category"),
                f.get("rule_ref"),
                f.get("artifact_id"),
                f.get("message"),
            )
            for f in findings
        )
        duplicates += sum(count - 1 for count in keys.values() if count > 1)
    counterexamples: list[dict[str, Any]] = []
    for _, run in pairs:
        report = run.report or {}
        for comparison in report.get("comparisons", ()):
            counterexamples.extend(comparison.get("counterexamples", ()))
    valid = sum(_counterexample_valid(item) for item in counterexamples)
    return FindingMetrics(
        findings_total=findings_total,
        defective_cases=len(defective),
        correct_defect_identification=_ratio(identification, len(defective)),
        correct_rule_reference=_ratio(cited_rules, identifying_total),
        correct_artifact=_ratio(cited_artifacts, identifying_total),
        correct_artifact_version=_ratio(cited_versions, identifying_total),
        evidence_location=_ratio(located, identifying_total),
        structured_evidence=_ratio(structured, identifying_total),
        valid_evidence=_ratio(valid_evidence, evidence_items),
        message_witness=_ratio(witnesses, identifying_total),
        valid_counterexamples=_ratio(valid, len(counterexamples)),
        blocker_severity_on_defects=_ratio(blockers, identifying_total),
        unsupported_findings=unsupported,
        duplicate_findings=duplicates,
    )


def _coverage_metrics(pairs: list[tuple[Case, ReviewRun]]) -> CoverageMetrics:
    entries: list[dict[str, Any]] = []
    code_entries: list[dict[str, Any]] = []
    modes: Counter[str] = Counter()
    unsupported = 0
    missing_evidence = 0
    for case, run in pairs:
        report = run.report or {}
        case_entries = list(report.get("coverage", ()))
        entries.extend(case_entries)
        if _bundle_links_code(case.bundle_path):
            code_entries.extend(case_entries)
        for comparison in report.get("comparisons", ()):
            mode = comparison.get("mode")
            if mode is not None:
                modes[str(mode)] += 1
        for finding in report.get("findings", ()):
            if finding.get("category") in UNSUPPORTED_CATEGORIES:
                unsupported += 1
            if finding.get("category") == "MISSING_EVIDENCE":
                missing_evidence += 1
    total = len(entries)
    return CoverageMetrics(
        total_effective_requirements=total,
        fully_reviewed=sum(_fully_reviewed(entry) for entry in entries),
        partially_reviewed=sum(_partially_reviewed(entry) for entry in entries),
        unreviewed=sum(not entry.get("covered") for entry in entries),
        unsupported_expressions=unsupported,
        missing_evidence=missing_evidence,
        source_coverage=_ratio(
            sum(
                any(str(item).startswith("requirement ") for item in entry.get("evidence", ()))
                for entry in entries
            ),
            total,
        ),
        specification_coverage=_ratio(
            sum(bool(entry.get("spec_elements")) for entry in entries), total
        ),
        workflow_coverage=_ratio(
            sum(bool(entry.get("workflow_elements")) for entry in entries), total
        ),
        code_coverage=_ratio(
            sum(entry.get("code_artifact") is not None for entry in code_entries),
            len(code_entries),
        ),
        evidence_completeness=_ratio(sum(bool(entry.get("evidence")) for entry in entries), total),
        comparison_modes=dict(modes),
    )


def _case_record(case: Case, run: ReviewRun) -> CaseRecord:
    report = run.report or {}
    return CaseRecord(
        bundle_id=case.bundle_id,
        expected_status=case.expected_status,
        actual_status=run.status or "ERROR",
        correct=run.status == case.expected_status,
        runtime_seconds=round(run.runtime_seconds, 3),
        input_bytes=run.input_bytes,
        cells_checked=_cells_checked(run),
        findings=len(report.get("findings", ())),
        comparison_modes=dict(
            Counter(str(comparison.get("mode")) for comparison in report.get("comparisons", ()))
        ),
        error=run.error,
    )


def _fully_reviewed(entry: dict[str, Any]) -> bool:
    return bool(entry.get("covered")) and bool(entry.get("evidence"))


def _partially_reviewed(entry: dict[str, Any]) -> bool:
    return not entry.get("covered") and bool(entry.get("evidence"))


def _cells_checked(run: ReviewRun) -> int:
    report = run.report or {}
    return sum(
        int(comparison.get("cells_checked", 0)) for comparison in report.get("comparisons", ())
    )


def _counterexample_valid(counterexample: dict[str, Any]) -> bool:
    return bool(counterexample.get("validated")) and (
        counterexample.get("requirement_outcome") != counterexample.get("artifact_outcome")
    )


def _analysis_requests(run: ReviewRun) -> int:
    return 1 if run.model_analysis is not None else 0


def _analysis_successes(run: ReviewRun) -> int:
    analysis = run.model_analysis or {}
    return 1 if analysis.get("status") == "completed" else 0


def _analysis_failures(run: ReviewRun) -> int:
    analysis = run.model_analysis or {}
    return 1 if analysis.get("status") == "failed" else 0


def _analysis_retries(run: ReviewRun) -> int:
    analysis = run.model_analysis or {}
    response = analysis.get("response") or {}
    return int(response.get("retry_count", 0) or 0)


def _fake_calls(run: ReviewRun) -> int:
    analysis = run.model_analysis or {}
    return 1 if analysis.get("synthetic") else 0


def _location_resolves(location: Any, bundle_path: Path) -> bool:
    if not isinstance(location, dict):
        return False
    relative = location.get("path")
    if not isinstance(relative, str) or not relative:
        return False
    target = (bundle_path / relative).resolve()
    try:
        target.relative_to(bundle_path.resolve())
    except ValueError:
        return False
    if not target.is_file():
        return False
    excerpt = location.get("excerpt")
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if location.get("kind") == "text":
        start = int(location.get("line_start", 0))
        end = int(location.get("line_end", 0))
        lines = text.splitlines()
        if start < 1 or end < start or end > len(lines):
            return False
        if excerpt:
            return str(excerpt).strip() in "\n".join(lines[start - 1 : end]).strip()
        return True
    if location.get("kind") == "json":
        try:
            document = json.loads(text)
            _resolve_pointer(document, str(location.get("pointer", "")))
        except (json.JSONDecodeError, ValueError, KeyError, IndexError, TypeError):
            return False
        return True
    return False


def _resolve_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError(f"invalid json pointer {pointer!r}")
    node = document
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(token)]
        elif isinstance(node, dict):
            node = node[token]
        else:
            raise KeyError(token)
    return node


def _artifact_versions(bundle_path: Path) -> dict[str, str]:
    try:
        payload = json.loads((bundle_path / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(artifact.get("artifact_id")): str(artifact.get("version"))
        for artifact in payload.get("artifacts", ())
        if artifact.get("artifact_id") is not None
    }


def _bundle_links_code(bundle_path: Path) -> bool:
    try:
        payload = json.loads((bundle_path / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("review_target", {}).get("code"))


def _input_bytes(bundle_path: Path) -> int:
    if not bundle_path.exists():
        return 0
    return sum(path.stat().st_size for path in bundle_path.rglob("*") if path.is_file())


__all__ = [
    "Case",
    "HarnessError",
    "ReviewRun",
    "Runner",
    "build_counterexamples_report",
    "build_coverage_report",
    "build_findings_report",
    "compute_metrics",
    "file_sha256",
    "git_revision",
    "header_for",
    "load_cases",
    "run_cases",
    "run_review",
    "write_json",
]
