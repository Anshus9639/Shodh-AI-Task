"""Write the development bundles and the sealed Stage 1 holdout.

Provenance hashes (artifact sha256 and approval-bound change hashes) are computed
here so that committed bundles always match their manifests. Expected holdout
labels are literals in this script and are written outside the bundle directories;
they are never produced from the implementation under test.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from verityflow.models.changes import ChangeArtifact

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "fixtures" / "dev"
HOLDOUT = ROOT / "holdout"

DICTIONARY = {
    "fields": [
        {
            "name": "category",
            "type": "string",
            "nullable": True,
            "domain": ["billing", "technical", "general"],
        },
        {"name": "priority", "type": "string", "nullable": True, "domain": ["urgent", "normal"]},
        {"name": "wait_time", "type": "integer", "unit": "minutes", "nullable": True},
        {"name": "vip", "type": "boolean", "nullable": True},
    ],
    "destinations": [
        {"id": "manual_review", "aliases": ["manual review"]},
        {"id": "priority_queue", "aliases": ["priority queue"]},
        {"id": "escalation_queue", "aliases": ["escalation queue"]},
        {"id": "normal_queue", "aliases": ["normal queue"]},
    ],
    "aliases": {"waiting": "wait_time"},
    "named_predicates": {"urgent ticket": {"kind": "eq", "field": "priority", "value": "urgent"}},
}

REQUIREMENTS = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, waiting >= 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
"""

SPEC = {
    "spec_id": "SPEC",
    "version": "1.0",
    "rules": [
        {
            "id": "S1",
            "priority": 1,
            "when": {"kind": "missing", "field": "category"},
            "destination": "manual_review",
        },
        {
            "id": "S2",
            "priority": 2,
            "when": {"kind": "eq", "field": "priority", "value": "urgent"},
            "destination": "priority_queue",
        },
        {
            "id": "S3",
            "priority": 3,
            "when": {
                "kind": "cmp",
                "field": "wait_time",
                "op": ">=",
                "value": 30,
                "unit": "minutes",
            },
            "destination": "escalation_queue",
        },
    ],
    "default": {"destination": "normal_queue"},
}

WORKFLOW = {
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
                    "condition": {"kind": "eq", "field": "priority", "value": "urgent"},
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
        {"id": "priority", "version": "1.0", "kind": "terminal", "destination": "priority_queue"},
        {
            "id": "escalation",
            "version": "1.0",
            "kind": "terminal",
            "destination": "escalation_queue",
        },
        {"id": "normal", "version": "1.0", "kind": "terminal", "destination": "normal_queue"},
    ],
}

THRESHOLD_CHANGE = {
    "changes": [
        {
            "change_id": "C1",
            "change_version": "1",
            "base_artifact": "REQ",
            "base_version": "1.0",
            "target_rule": "R3",
            "operation": "replace_threshold",
            "from": {"value": 30, "unit": "minutes"},
            "to": {"value": 20, "unit": "minutes"},
            "sequence": 1,
            "rationale": "Risk review lowered the escalation threshold to 20 minutes.",
        }
    ]
}


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _change_hash(changes: dict[str, object]) -> str:
    artifact = ChangeArtifact.model_validate(changes)
    return artifact.changes[0].content_hash()


def write_bundle(
    root: Path,
    bundle_id: str,
    *,
    requirements: str = REQUIREMENTS,
    spec: dict[str, object] = SPEC,
    workflow: dict[str, object] = WORKFLOW,
    changes: dict[str, object] | None = None,
    approvals: list[dict[str, object]] | None = None,
) -> list[Path]:
    files: dict[str, tuple[str, str, str, str]] = {
        "dictionary.json": ("DICT", "1.0", "data_dictionary", _json(DICTIONARY)),
        "requirements.txt": ("REQ", "1.0", "requirements", requirements),
        "spec.json": ("SPEC", "1.0", "spec", _json(spec)),
        "workflow.json": ("WF", "1.0", "workflow", _json(workflow)),
    }
    if changes is not None:
        files["changes.json"] = ("CHG", "1.0", "change_requests", _json(changes))
    artifacts = [
        {
            "artifact_id": artifact_id,
            "version": version,
            "kind": kind,
            "path": path,
            "sha256": _digest(content),
        }
        for path, (artifact_id, version, kind, content) in files.items()
    ]
    review_target: dict[str, object] = {
        "requirements": ["REQ@1.0"],
        "spec": "SPEC@1.0",
        "workflow": "WF@1.0",
    }
    manifest = {
        "bundle_id": bundle_id,
        "bundle_version": "1.0",
        "review_target": review_target,
        "artifacts": artifacts,
        "approvals": approvals or [],
    }
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for path, (_, _, _, content) in files.items():
        target = root / path
        target.write_text(content, encoding="utf-8", newline="\n")
        written.append(target)
    manifest_path = root / "bundle.json"
    manifest_path.write_text(_json(manifest), encoding="utf-8", newline="\n")
    written.append(manifest_path)
    return written


def _approval(
    approval_id: str,
    changes: dict[str, object],
    decision: str,
    *,
    approver: str,
    role: str = "policy_owner",
    supersedes: list[str] | None = None,
) -> dict[str, object]:
    return {
        "approval_id": approval_id,
        "change_id": "C1",
        "change_version": "1",
        "change_sha256": _change_hash(changes),
        "decision": decision,
        "approver_id": approver,
        "approver_role": role,
        "recorded_at": "2026-02-01T10:00:00Z",
        "supersedes": supersedes or [],
    }


def _reordered_spec() -> dict[str, object]:
    rules = list(SPEC["rules"])  # type: ignore[arg-type]
    first = {**rules[1], "priority": rules[0]["priority"]}
    second = {**rules[0], "priority": rules[1]["priority"]}
    return {**SPEC, "rules": [first, second, rules[2]]}


def _renamed_spec() -> dict[str, object]:
    rules = list(SPEC["rules"])  # type: ignore[arg-type]
    return {
        "spec_id": "RENAMED",
        "version": "1.0",
        "rules": [
            {
                "id": "S-ALPHA",
                "priority": 10,
                "when": rules[0]["when"],
                "destination": "manual_review",
            },
            {
                "id": "S-BETA",
                "priority": 20,
                "when": rules[1]["when"],
                "destination": "priority_queue",
            },
            {
                "id": "S-GAMMA",
                "priority": 30,
                "when": rules[2]["when"],
                "destination": "escalation_queue",
            },
        ],
        "default": {"destination": "normal_queue"},
    }


def _renamed_workflow() -> dict[str, object]:
    workflow = json.loads(_json(WORKFLOW))
    branches = workflow["nodes"][0]["branches"]
    for index, branch in enumerate(branches, start=1):
        branch["branch_id"] = f"route-{index}"
    return workflow


def _write_labels(name: str, expected: dict[str, object]) -> Path:
    labels_dir = HOLDOUT / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    path = labels_dir / f"{name}.json"
    path.write_text(
        _json({"bundle": name, "stage": 1, "expected": expected}),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _holdout_manifest(paths: list[Path]) -> None:
    lines = []
    for path in sorted(paths):
        relative = path.relative_to(HOLDOUT).as_posix()
        lines.append(f"{_digest(path.read_text(encoding='utf-8'))}  {relative}")
    (HOLDOUT / "MANIFEST.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    dev_paths: list[Path] = []
    dev_paths += write_bundle(DEV / "B01_starter", "B01_starter")
    dev_paths += write_bundle(
        DEV / "B03_approved_change",
        "B03_approved_change",
        changes=THRESHOLD_CHANGE,
        approvals=[
            _approval("A1", THRESHOLD_CHANGE, "approve", approver="alice"),
        ],
    )
    dev_paths += write_bundle(
        DEV / "B05_unapproved_change",
        "B05_unapproved_change",
        changes=THRESHOLD_CHANGE,
    )
    dev_paths += write_bundle(
        DEV / "B06_conflicting_approvals",
        "B06_conflicting_approvals",
        changes=THRESHOLD_CHANGE,
        approvals=[
            _approval("A2", THRESHOLD_CHANGE, "approve", approver="alice"),
            _approval("A3", THRESHOLD_CHANGE, "reject", approver="bob"),
        ],
    )

    holdout_paths: list[Path] = []
    clean = {
        "parse": {"rules": 4, "gap": False},
        "mapping": {"spec_complete": True, "workflow_complete": True},
        "resolution": {"confirmed_candidate_id": "confirmed", "undetermined": []},
        "finding_categories": [],
    }
    holdout_paths += write_bundle(HOLDOUT / "bundles" / "H01_clean", "H01_clean")
    _write_labels("H01_clean", clean)
    holdout_paths += write_bundle(
        HOLDOUT / "bundles" / "H02_renamed",
        "H02_renamed",
        spec=_renamed_spec(),
        workflow=_renamed_workflow(),
    )
    _write_labels("H02_renamed", clean)
    holdout_paths += write_bundle(
        HOLDOUT / "bundles" / "H03_spec_reordered",
        "H03_spec_reordered",
        spec=_reordered_spec(),
    )
    _write_labels(
        "H03_spec_reordered",
        {
            "parse": {"rules": 4, "gap": False},
            "mapping": {"spec_complete": False, "workflow_complete": True},
            "resolution": {"confirmed_candidate_id": "confirmed", "undetermined": []},
            "finding_categories": ["UNTRACEABLE_ELEMENT", "UNTRACEABLE_REQUIREMENT"],
        },
    )
    holdout_paths += write_bundle(
        HOLDOUT / "bundles" / "H04_approved_threshold",
        "H04_approved_threshold",
        changes=THRESHOLD_CHANGE,
        approvals=[_approval("A4", THRESHOLD_CHANGE, "approve", approver="carol")],
    )
    _write_labels(
        "H04_approved_threshold",
        {
            "parse": {"rules": 4, "gap": False},
            "mapping": {"spec_complete": True, "workflow_complete": True},
            "resolution": {
                "confirmed_candidate_id": "confirmed",
                "applied": ["C1@1"],
                "undetermined": [],
                "rule_thresholds": {"R3": "20"},
            },
            "finding_categories": [],
        },
    )
    tampered = _approval("A5", THRESHOLD_CHANGE, "approve", approver="dave")
    tampered["change_sha256"] = "0" * 64
    holdout_paths += write_bundle(
        HOLDOUT / "bundles" / "H05_tampered_approval",
        "H05_tampered_approval",
        changes=THRESHOLD_CHANGE,
        approvals=[tampered],
    )
    _write_labels(
        "H05_tampered_approval",
        {
            "parse": {"rules": 4, "gap": False},
            "mapping": {"spec_complete": True, "workflow_complete": True},
            "resolution": {
                "confirmed_candidate_id": "confirmed",
                "applied": [],
                "undetermined": [],
                "decision_states": {"C1": "unapproved"},
            },
            "finding_categories": ["INVALID_APPROVAL", "UNAPPROVED_CHANGE"],
        },
    )
    _holdout_manifest(holdout_paths)

    for path in [*dev_paths, *holdout_paths]:
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
