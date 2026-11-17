"""Write the development bundles and reserve the sealed Stage 1 holdout.

Provenance hashes (artifact sha256 and approval-bound change hashes) are computed
here so that committed bundles always match their manifests. Expected holdout
labels are literals in this script and are written outside the bundle directories;
they are never produced from the implementation under test.

The original H01-H05 set was inspected during Stage 1 debugging. It now lives
under ``fixtures/dev_evidence/holdout_v1`` as development evidence and is frozen;
this script no longer regenerates it. The sealed holdout under ``holdout/`` uses a
separate claims-triage domain and was authored before any final evaluation run.
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

THRESHOLD_CHANGE_25 = {
    "changes": [
        {
            "change_id": "C1",
            "change_version": "1",
            "base_artifact": "REQ",
            "base_version": "1.0",
            "target_rule": "R3",
            "operation": "replace_threshold",
            "from": {"value": 30, "unit": "minutes"},
            "to": {"value": 25, "unit": "minutes"},
            "sequence": 1,
            "rationale": "Policy owner approved lowering the escalation threshold to 25 minutes.",
        }
    ]
}

CLAIM_DICTIONARY = {
    "fields": [
        {
            "name": "segment",
            "type": "string",
            "nullable": True,
            "domain": ["auto", "property", "health"],
        },
        {"name": "risk", "type": "integer", "nullable": True, "min": 0, "max": 100},
        {"name": "amount", "type": "decimal", "unit": "usd", "scale": 2, "nullable": True},
        {"name": "documents", "type": "boolean", "nullable": True},
    ],
    "destinations": [
        {"id": "manual_hold", "aliases": ["manual hold"]},
        {"id": "fraud_investigation", "aliases": ["fraud investigation"]},
        {"id": "specialist_review", "aliases": ["specialist review"]},
        {"id": "standard_payout", "aliases": ["standard payout"]},
    ],
    "aliases": {},
    "named_predicates": {},
}

CLAIM_REQUIREMENTS = """\
K1. Missing segment -> manual hold.
K2. Otherwise, risk >= 80 -> fraud investigation.
K3. Otherwise, amount >= 5000 usd -> specialist review.
K4. All remaining tickets -> standard payout.
"""

CLAIM_SPEC = {
    "spec_id": "CLAIMS",
    "version": "1.0",
    "rules": [
        {
            "id": "Q1",
            "priority": 1,
            "when": {"kind": "missing", "field": "segment"},
            "destination": "manual_hold",
        },
        {
            "id": "Q2",
            "priority": 2,
            "when": {"kind": "cmp", "field": "risk", "op": ">=", "value": 80},
            "destination": "fraud_investigation",
        },
        {
            "id": "Q3",
            "priority": 3,
            "when": {
                "kind": "cmp",
                "field": "amount",
                "op": ">=",
                "value": 5000,
                "unit": "usd",
            },
            "destination": "specialist_review",
        },
    ],
    "default": {"destination": "standard_payout"},
}

CLAIM_WORKFLOW = {
    "workflow_id": "CLAIM_WF",
    "version": "1.0",
    "start": "intake",
    "nodes": [
        {
            "id": "intake",
            "version": "1.0",
            "kind": "decision",
            "branches": [
                {
                    "branch_id": "r1",
                    "version": "1.0",
                    "condition": {"kind": "missing", "field": "segment"},
                    "target": "hold",
                },
                {
                    "branch_id": "r2",
                    "version": "1.0",
                    "condition": {"kind": "cmp", "field": "risk", "op": ">=", "value": 80},
                    "target": "fraud",
                },
                {
                    "branch_id": "r3",
                    "version": "1.0",
                    "condition": {
                        "kind": "cmp",
                        "field": "amount",
                        "op": ">=",
                        "value": 5000,
                        "unit": "usd",
                    },
                    "target": "specialist",
                },
                {"branch_id": "r4", "version": "1.0", "target": "payout"},
            ],
        },
        {"id": "hold", "version": "1.0", "kind": "terminal", "destination": "manual_hold"},
        {
            "id": "fraud",
            "version": "1.0",
            "kind": "terminal",
            "destination": "fraud_investigation",
        },
        {
            "id": "specialist",
            "version": "1.0",
            "kind": "terminal",
            "destination": "specialist_review",
        },
        {"id": "payout", "version": "1.0", "kind": "terminal", "destination": "standard_payout"},
    ],
}

CLAIM_CHANGE = {
    "changes": [
        {
            "change_id": "C1",
            "change_version": "1",
            "base_artifact": "REQ",
            "base_version": "1.0",
            "target_rule": "K3",
            "operation": "replace_threshold",
            "from": {"value": 5000, "unit": "usd"},
            "to": {"value": 3000, "unit": "usd"},
            "sequence": 1,
            "rationale": "Claims leadership approved lowering specialist review to 3000 usd.",
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
    dictionary: dict[str, object] = DICTIONARY,
    requirements: str = REQUIREMENTS,
    spec: dict[str, object] = SPEC,
    workflow: dict[str, object] = WORKFLOW,
    changes: dict[str, object] | None = None,
    approvals: list[dict[str, object]] | None = None,
) -> list[Path]:
    files: dict[str, tuple[str, str, str, str]] = {
        "dictionary.json": ("DICT", "1.0", "data_dictionary", _json(dictionary)),
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


def _renamed_claim_spec() -> dict[str, object]:
    rules = list(CLAIM_SPEC["rules"])  # type: ignore[arg-type]
    return {
        "spec_id": "CLAIMS-RENAMED",
        "version": "1.0",
        "rules": [
            {
                "id": f"QX-{index}",
                "priority": index * 10,
                "when": rule["when"],
                "destination": rule["destination"],
            }
            for index, rule in enumerate(rules, start=1)
        ],
        "default": {"destination": "standard_payout"},
    }


def _renamed_claim_workflow() -> dict[str, object]:
    workflow = json.loads(_json(CLAIM_WORKFLOW))
    for index, branch in enumerate(workflow["nodes"][0]["branches"], start=1):
        branch["branch_id"] = f"route-{index}"
    return workflow


def _write_labels(root: Path, name: str, expected: dict[str, object]) -> Path:
    labels_dir = root / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    path = labels_dir / f"{name}.json"
    path.write_text(
        _json({"bundle": name, "stage": 1, "expected": expected}),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _write_manifest(root: Path, paths: list[Path]) -> Path:
    lines = []
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        lines.append(f"{_digest(path.read_text(encoding='utf-8'))}  {relative}")
    manifest = root / "MANIFEST.sha256"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return manifest


def _write_generation(labels: list[Path], names: list[str]) -> Path:
    payload = {
        "holdout_id": "stage1_final_v1",
        "reserved_on": "2026-11-17",
        "generated_by": "scripts/build_fixtures.py",
        "generator_sha256": _digest(Path(__file__).read_text(encoding="utf-8")),
        "config": {
            "path": "configs/default.toml",
            "sha256": _digest((ROOT / "configs" / "default.toml").read_text(encoding="utf-8")),
        },
        "implementation_checkpoint": "04d9ea6",
        "docs_checkpoint": "9f55815",
        "domain": "claims triage (segment, risk, amount, documents)",
        "bundles": names,
        "labels": sorted(path.stem for path in labels),
        "policy": (
            "Authored before any final-evaluation run. The pipeline is not executed "
            "against these bundles during Stage 1 development."
        ),
    }
    target = HOLDOUT / "GENERATION.json"
    target.write_text(_json(payload), encoding="utf-8", newline="\n")
    return target


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
    dev_paths += write_bundle(
        DEV / "B07_approved_threshold_25",
        "B07_approved_threshold_25",
        changes=THRESHOLD_CHANGE_25,
        approvals=[
            _approval("A7", THRESHOLD_CHANGE_25, "approve", approver="alice"),
        ],
    )
    dev_paths += write_bundle(
        DEV / "B08_unapproved_threshold_25",
        "B08_unapproved_threshold_25",
        changes=THRESHOLD_CHANGE_25,
    )
    dev_paths += write_bundle(
        DEV / "B09_conflicting_threshold_25",
        "B09_conflicting_threshold_25",
        changes=THRESHOLD_CHANGE_25,
        approvals=[
            _approval("A8", THRESHOLD_CHANGE_25, "approve", approver="alice"),
            _approval("A9", THRESHOLD_CHANGE_25, "reject", approver="bob"),
        ],
    )

    routes = {
        "missing_segment": "manual_hold",
        "risk_high": "fraud_investigation",
        "amount_high": "specialist_review",
        "baseline": "standard_payout",
    }
    clean = {
        "parse": {"rules": 4, "gap": False},
        "mapping": {"spec_complete": True, "workflow_complete": True},
        "resolution": {"confirmed_candidate_id": "confirmed", "undetermined": []},
        "finding_categories": [],
        "routes": routes,
    }

    holdout_paths: list[Path] = []
    label_paths: list[Path] = []
    holdout_paths += write_bundle(
        HOLDOUT / "bundles" / "F01_clean",
        "F01_clean",
        requirements=CLAIM_REQUIREMENTS,
        dictionary=CLAIM_DICTIONARY,
        spec=CLAIM_SPEC,
        workflow=CLAIM_WORKFLOW,
    )
    label_paths.append(_write_labels(HOLDOUT, "F01_clean", clean))

    holdout_paths += write_bundle(
        HOLDOUT / "bundles" / "F02_renamed",
        "F02_renamed",
        requirements=CLAIM_REQUIREMENTS,
        dictionary=CLAIM_DICTIONARY,
        spec=_renamed_claim_spec(),
        workflow=_renamed_claim_workflow(),
    )
    label_paths.append(_write_labels(HOLDOUT, "F02_renamed", clean))

    holdout_paths += write_bundle(
        HOLDOUT / "bundles" / "F03_approved_threshold",
        "F03_approved_threshold",
        requirements=CLAIM_REQUIREMENTS,
        dictionary=CLAIM_DICTIONARY,
        spec=CLAIM_SPEC,
        workflow=CLAIM_WORKFLOW,
        changes=CLAIM_CHANGE,
        approvals=[_approval("F3", CLAIM_CHANGE, "approve", approver="carol")],
    )
    label_paths.append(
        _write_labels(
            HOLDOUT,
            "F03_approved_threshold",
            {
                "parse": {"rules": 4, "gap": False},
                "mapping": {"spec_complete": True, "workflow_complete": True},
                "resolution": {
                    "confirmed_candidate_id": "confirmed",
                    "applied": ["C1@1"],
                    "undetermined": [],
                    "rule_thresholds": {"K3": "3000"},
                },
                "finding_categories": [],
                "routes": {
                    **routes,
                    "amount_above_new_threshold": "specialist_review",
                    "amount_below_new_threshold": "standard_payout",
                },
            },
        )
    )
    holdout_paths += write_bundle(
        HOLDOUT / "bundles" / "F04_unapproved_threshold",
        "F04_unapproved_threshold",
        requirements=CLAIM_REQUIREMENTS,
        dictionary=CLAIM_DICTIONARY,
        spec=CLAIM_SPEC,
        workflow=CLAIM_WORKFLOW,
        changes=CLAIM_CHANGE,
    )
    label_paths.append(
        _write_labels(
            HOLDOUT,
            "F04_unapproved_threshold",
            {
                "parse": {"rules": 4, "gap": False},
                "mapping": {"spec_complete": True, "workflow_complete": True},
                "resolution": {
                    "confirmed_candidate_id": "confirmed",
                    "applied": [],
                    "undetermined": [],
                    "decision_states": {"C1": "unapproved"},
                    "rule_thresholds": {"K3": "5000"},
                },
                "finding_categories": ["UNAPPROVED_CHANGE"],
                "routes": routes,
            },
        )
    )

    tampered = _approval("F5", CLAIM_CHANGE, "approve", approver="dave")
    tampered["change_sha256"] = "0" * 64
    holdout_paths += write_bundle(
        HOLDOUT / "bundles" / "F05_tampered_approval",
        "F05_tampered_approval",
        requirements=CLAIM_REQUIREMENTS,
        dictionary=CLAIM_DICTIONARY,
        spec=CLAIM_SPEC,
        workflow=CLAIM_WORKFLOW,
        changes=CLAIM_CHANGE,
        approvals=[tampered],
    )
    label_paths.append(
        _write_labels(
            HOLDOUT,
            "F05_tampered_approval",
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
                "routes": routes,
            },
        )
    )

    generation = _write_generation(
        label_paths,
        [
            "F01_clean",
            "F02_renamed",
            "F03_approved_threshold",
            "F04_unapproved_threshold",
            "F05_tampered_approval",
        ],
    )
    holdout_paths.extend(label_paths)
    holdout_paths.append(generation)
    _write_manifest(HOLDOUT, holdout_paths)

    for path in dev_paths:
        print(path.relative_to(ROOT))
    for path in holdout_paths:
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
