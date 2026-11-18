"""Reproduce the two Stage 4 debugging examples from their real git history.

Each example is replayed against an isolated `git archive` snapshot of the
commit that introduced the fix's parent. The regression test is taken from the
fix commit, so the parent source is exercised by exactly the assertion that was
added with the correction. The same test then runs against the fix snapshot and
the current HEAD, which demonstrates the correction and its persistence.

Nothing here rewrites history: diffs, test names and outputs are read from the
repository's own commits.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EVALUATION = ROOT / "evaluation"
REPORTS = ROOT / "reports"
sys.path.insert(0, str(EVALUATION))
sys.path.insert(0, str(ROOT / "src"))

import harness  # noqa: E402

from verityflow.evaluation.models import DebuggingExamplesReport  # noqa: E402
from verityflow.schemas import build_schemas  # noqa: E402

TEST_FILE = "tests/integration/test_review.py"


@dataclass(frozen=True)
class Incident:
    example_id: str
    title: str
    fix_commit: str
    parent_commit: str
    test_name: str
    source_files: tuple[str, ...]
    diagnosis: str
    root_cause: str
    support_paths: tuple[str, ...] = ()


EXAMPLES = (
    Incident(
        example_id="D-01",
        title="A sampled review asserted a cause for a requirement the sample never exercised",
        fix_commit="528aaf7",
        parent_commit="ace9479",
        test_name="test_sampled_space_is_incomplete_and_disclosed",
        source_files=(
            "src/verityflow/verify/interactions.py",
            "src/verityflow/verify/review.py",
        ),
        diagnosis=(
            "The sampled B10 review emitted an INTERACTION/COVERAGE_GAP finding whose message "
            "said the unexercised rule 'is shadowed by earlier requirements or cannot be "
            "satisfied'. An eight-cell sample of a 2,400-cell space cannot establish either "
            "cause. The added assertion rejected the shadow language and required the "
            "message to say the rule was not exercised and its firing is unreviewed; the "
            "assertion failed against the parent source."
        ),
        root_cause=(
            "analyze_interactions formatted one fixed message for every never-fired rule. "
            "The exhaustive case genuinely observes that a rule never fires in the whole "
            "space, but the sampled case only observes absence from the evaluated sample. "
            "The formatter conflated the two, and review.py's interaction UnreviewedScope "
            "used the same unconditional wording."
        ),
    ),
    Incident(
        example_id="D-02",
        title="An unsupported router region was found but omitted from the unreviewed scope",
        fix_commit="f0dbb0c",
        parent_commit="528aaf7",
        test_name="test_sampled_blocker_with_unsupported_code_keeps_both_disclosures",
        source_files=("src/verityflow/verify/review.py",),
        support_paths=("fixtures/dev/B14_router_unsupported",),
        diagnosis=(
            "B14_router_unsupported reviewed with max_cells=12 contains a validated "
            "wait_time=30 blocker and an undeclared-destination region in the router. At the "
            "parent commit the blocker did decide CHANGES_REQUIRED, the validated "
            "counterexample was re-routed, and UNSUPPORTED_CODE/COVERAGE_GAP findings were "
            "present, but the unsupported region was absent from the machine-readable "
            "unreviewed scopes. The added assertion that requires a 'code' scope for "
            "ROUTER@1.0 failed against the parent source."
        ),
        root_cause=(
            "_verify_artifacts appended UNSUPPORTED_CODE findings to the findings list but "
            "never added the corresponding UnreviewedScope, so the report's unreviewed list "
            "silently omitted a router region the interpreter could not evaluate. The same "
            "commit also pinned the blocker-first status precedence - implemented since "
            "4702939 but untested and contradicted by documentation claiming sampled status "
            "was capped at INCOMPLETE - with tests A-E."
        ),
    ),
)


class ExampleError(RuntimeError):
    """A snapshot or test execution could not be prepared."""


def _git(*arguments: str, binary: bool = False) -> bytes | str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", "replace").strip()
        raise ExampleError(f"git {' '.join(arguments)} failed: {message}")
    if binary:
        return completed.stdout
    return completed.stdout.decode("utf-8", "replace")


def _resolve(commit: str) -> str:
    return str(_git("rev-parse", f"{commit}^{{commit}}")).strip()


def _snapshot(commit: str, cache_root: Path) -> Path:
    resolved = _resolve(commit)
    target = cache_root / resolved[:12]
    marker = target / ".snapshot-commit"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == resolved:
        return target
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    archive = _git("archive", "--format=tar", resolved, binary=True)
    assert isinstance(archive, bytes)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(target, filter="data")
    marker.write_text(resolved + "\n", encoding="utf-8")
    return target


def _run_test(snapshot: Path, test_name: str) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONPATH": str(snapshot / "src"),
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    environment.pop("PYTEST_ADDOPTS", None)
    command = [
        sys.executable,
        "-m",
        "pytest",
        f"{TEST_FILE}::{test_name}",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=snapshot,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    elapsed = round(time.perf_counter() - started, 3)
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return {
        "command": "python -m pytest " + " ".join(command[3:]) + "  (PYTHONPATH=<snapshot>\\src)",
        "exit_code": completed.returncode,
        "output": output.strip(),
        "runtime_seconds": elapsed,
    }


def _scenario(
    incident: Incident,
    cache_root: Path,
    label: str,
    commit: str,
    *,
    test_from: str | None,
    support_from: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    snapshot = _snapshot(commit, cache_root)
    if test_from is not None:
        fixed_test = str(_git("show", f"{_resolve(test_from)}:{TEST_FILE}"))
        (snapshot / TEST_FILE).write_text(fixed_test, encoding="utf-8")
    if support_from is not None:
        source = _snapshot(support_from, cache_root)
        for relative in incident.support_paths:
            destination = snapshot / relative
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source / relative, destination)
    result = _run_test(snapshot, incident.test_name)
    result["commit"] = _resolve(commit)
    result["label"] = label
    return snapshot, result


def build_example(incident: Incident, cache_root: Path) -> dict[str, Any]:
    _, failing = _scenario(
        incident,
        cache_root,
        "parent source with the regression test from the fix commit",
        incident.parent_commit,
        test_from=incident.fix_commit,
        support_from=incident.fix_commit if incident.support_paths else None,
    )
    _, fixed = _scenario(
        incident,
        cache_root,
        "fix commit source and test",
        incident.fix_commit,
        test_from=None,
    )
    _, current = _scenario(
        incident,
        cache_root,
        "current HEAD",
        "HEAD",
        test_from=None,
    )
    if failing["exit_code"] == 0:
        raise ExampleError(f"{incident.example_id} did not fail at its parent commit")
    for run in (fixed, current):
        if run["exit_code"] != 0:
            raise ExampleError(
                f"{incident.example_id} failed after the fix at {run['commit']}: {run['output']}"
            )
    code_change = str(
        _git(
            "diff",
            f"{_resolve(incident.parent_commit)}",
            f"{_resolve(incident.fix_commit)}",
            "--",
            *incident.source_files,
        )
    )
    test_change = str(
        _git(
            "diff",
            f"{_resolve(incident.parent_commit)}",
            f"{_resolve(incident.fix_commit)}",
            "--",
            TEST_FILE,
        )
    )
    return {
        "example_id": incident.example_id,
        "title": incident.title,
        "origin": "docs/debugging_log.md entry reproduced from git history",
        "fix_commit": _resolve(incident.fix_commit),
        "parent_commit": _resolve(incident.parent_commit),
        "failing_run": failing,
        "diagnosis": incident.diagnosis,
        "root_cause": incident.root_cause,
        "code_change": code_change,
        "code_change_files": list(incident.source_files),
        "regression_test": {
            "test_id": f"{TEST_FILE}::{incident.test_name}",
            "test_change": test_change,
        },
        "result_after_fix": fixed,
        "current_head_run": current,
        "verification": (
            "The failing run executes the parent commit's source with the fix commit's "
            "regression test in an isolated git archive snapshot; the passing runs execute "
            "the fix commit and current HEAD with their own test files."
        ),
    }


def build_report(cache_root: Path | None = None) -> DebuggingExamplesReport:
    root = cache_root or Path(tempfile.gettempdir()) / "verityflow-debug-snapshots"
    root.mkdir(parents=True, exist_ok=True)
    examples = tuple(build_example(incident, root) for incident in EXAMPLES)
    header = harness.header_for(
        "debugging",
        bundle_count=0,
        model_execution_mode="off (deterministic; no model requests)",
    )
    return DebuggingExamplesReport(header=header, examples=examples)


def write_report(report: DebuggingExamplesReport) -> Path:
    schemas = build_schemas()
    payload = report.model_dump(mode="json")
    import jsonschema

    jsonschema.Draft202012Validator(schemas["evaluation_debugging"]).validate(payload)
    directory = REPORTS / "debugging"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "debugging_examples.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (directory / "REPORT.md").write_text(_markdown(report), encoding="utf-8")
    return path


def _markdown(report: DebuggingExamplesReport) -> str:
    lines = [
        "# Debugging examples",
        "",
        "Reproduced from git history by `scripts/reproduce_debugging_examples.py`.",
        "",
    ]
    for example in report.examples:
        failing = example["failing_run"]
        fixed = example["result_after_fix"]
        current = example["current_head_run"]
        lines.extend(
            [
                f"## {example['example_id']} — {example['title']}",
                "",
                f"- Parent commit: `{example['parent_commit']}`",
                f"- Fix commit: `{example['fix_commit']}`",
                f"- Regression test: `{example['regression_test']['test_id']}`",
                f"- Parent result: exit {failing['exit_code']} in {failing['runtime_seconds']} s",
                f"- Fix result: exit {fixed['exit_code']} in {fixed['runtime_seconds']} s",
                f"- Current HEAD result: exit {current['exit_code']} "
                f"in {current['runtime_seconds']} s",
                "",
                "### Actual failing output",
                "",
                "```text",
                failing["output"],
                "```",
                "",
                "### Diagnosis",
                "",
                example["diagnosis"],
                "",
                "### Root cause",
                "",
                example["root_cause"],
                "",
                "### Code change",
                "",
                "```diff",
                example["code_change"].rstrip(),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def main() -> int:
    report = build_report()
    path = write_report(report)
    print(f"wrote {path.relative_to(ROOT)}")
    for example in report.examples:
        print(
            f"{example['example_id']}: parent exit {example['failing_run']['exit_code']}, "
            f"fix exit {example['result_after_fix']['exit_code']}, "
            f"HEAD exit {example['current_head_run']['exit_code']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
