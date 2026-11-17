"""Write the sealed Stage 2 semantic holdout (G01-G05).

The set is authored and frozen before any Stage 2 verification algorithm exists.
Artifact hashes and approval-bound change hashes are computed here; expected
labels are literals in this script and are written outside every bundle
directory. Labels are derived by hand from the requirement order and the
change/approval records, never from the implementation under test.

The seed selects one distinct threshold formulation per bundle from a vetted
pool of semantically equivalent phrasings, so the frozen set exercises unseen
rule IDs and paraphrases without random drift.

Do not regenerate after the freezing commit. Ordinary development tests only
hash the set; they never load it through the reviewer.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from verityflow.models.changes import ChangeArtifact

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "holdout_stage2"
BUNDLES = OUT / "bundles"
LABELS = OUT / "labels"
SEED = 20261117

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

THRESHOLD_PHRASES = (
    "waiting >= 30 minutes",
    "waiting is at least 30 minutes",
    "waiting > 29 minutes",
    "not waiting < 30 minutes",
    "waiting is greater than or equal to 30 minutes",
)

BUNDLE_NAMES = (
    "G01_valid_original",
    "G02_valid_approved_25",
    "G03_precedence_defect",
    "G04_code_boundary_defect",
    "G05_conflicting_approvals",
)

ROUTER_SOURCE = '''\
"""Linked router for the support-ticket workflow."""

DEFAULT_DESTINATION = "normal_queue"


def route(ticket):
    if "category" not in ticket or ticket.get("category") is None:
        return "manual_review"
    if ticket.get("priority") == "urgent":
        return "priority_queue"
    if ticket.get("wait_time") is not None and ticket.get("wait_time") > 30:
        return "escalation_queue"
    return DEFAULT_DESTINATION
'''


def requirements_text(phrase: str) -> str:
    return (
        "T1. Missing category -> manual review.\n"
        "T2. Otherwise, urgent ticket -> priority queue.\n"
        f"T3. Otherwise, {phrase} -> escalation queue.\n"
        "T4. All remaining tickets -> normal queue.\n"
    )


def spec(threshold: int, *, wait_first: bool = False) -> dict[str, object]:
    rules = [
        {
            "id": "TR-MISSING",
            "priority": 20 if wait_first else 10,
            "when": {"kind": "missing", "field": "category"},
            "destination": "manual_review",
        },
        {
            "id": "TR-URGENT",
            "priority": 30 if wait_first else 20,
            "when": {"kind": "eq", "field": "priority", "value": "urgent"},
            "destination": "priority_queue",
        },
        {
            "id": "TR-WAIT",
            "priority": 10 if wait_first else 30,
            "when": {
                "kind": "cmp",
                "field": "wait_time",
                "op": ">=",
                "value": threshold,
                "unit": "minutes",
            },
            "destination": "escalation_queue",
        },
    ]
    return {
        "spec_id": "TRIAGE-SPEC",
        "version": "1.0",
        "rules": rules,
        "default": {"destination": "normal_queue"},
    }


def workflow(threshold: int, *, wait_first: bool = False) -> dict[str, object]:
    missing = {
        "branch_id": "rb-missing",
        "version": "1.0",
        "condition": {"kind": "missing", "field": "category"},
        "target": "manual_node",
    }
    urgent = {
        "branch_id": "rb-urgent",
        "version": "1.0",
        "condition": {"kind": "eq", "field": "priority", "value": "urgent"},
        "target": "priority_node",
    }
    wait = {
        "branch_id": "rb-wait",
        "version": "1.0",
        "condition": {
            "kind": "cmp",
            "field": "wait_time",
            "op": ">=",
            "value": threshold,
            "unit": "minutes",
        },
        "target": "escalation_node",
    }
    fallback = {"branch_id": "rb-default", "version": "1.0", "target": "normal_node"}
    branches = (
        [wait, missing, urgent, fallback] if wait_first else [missing, urgent, wait, fallback]
    )
    return {
        "workflow_id": "TRIAGE-WF",
        "version": "1.0",
        "start": "intake",
        "nodes": [
            {"id": "intake", "version": "1.0", "kind": "decision", "branches": branches},
            {
                "id": "manual_node",
                "version": "1.0",
                "kind": "terminal",
                "destination": "manual_review",
            },
            {
                "id": "priority_node",
                "version": "1.0",
                "kind": "terminal",
                "destination": "priority_queue",
            },
            {
                "id": "escalation_node",
                "version": "1.0",
                "kind": "terminal",
                "destination": "escalation_queue",
            },
            {
                "id": "normal_node",
                "version": "1.0",
                "kind": "terminal",
                "destination": "normal_queue",
            },
        ],
    }


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


THRESHOLD_CHANGE = {
    "changes": [
        {
            "change_id": "C1",
            "change_version": "1",
            "base_artifact": "REQ",
            "base_version": "1.0",
            "target_rule": "T3",
            "operation": "replace_threshold",
            "from": {"value": 30, "unit": "minutes"},
            "to": {"value": 25, "unit": "minutes"},
            "sequence": 1,
            "rationale": "Support leadership approved lowering the escalation threshold to 25.",
        }
    ]
}


def _change_hash(changes: dict[str, object]) -> str:
    artifact = ChangeArtifact.model_validate(changes)
    return artifact.changes[0].content_hash()


def _approval(
    approval_id: str, changes: dict[str, object], decision: str, *, approver: str
) -> dict[str, object]:
    return {
        "approval_id": approval_id,
        "change_id": "C1",
        "change_version": "1",
        "change_sha256": _change_hash(changes),
        "decision": decision,
        "approver_id": approver,
        "approver_role": "policy_owner",
        "recorded_at": "2026-03-02T09:00:00Z",
        "supersedes": [],
    }


def write_bundle(
    root: Path,
    bundle_id: str,
    *,
    requirements: str,
    spec_artifact: dict[str, object],
    workflow_artifact: dict[str, object],
    changes: dict[str, object] | None = None,
    approvals: list[dict[str, object]] | None = None,
    code: str | None = None,
) -> list[Path]:
    files: dict[str, tuple[str, str, str, str]] = {
        "dictionary.json": ("DICT", "1.0", "data_dictionary", _json(DICTIONARY)),
        "requirements.txt": ("REQ", "1.0", "requirements", requirements),
        "spec.json": ("SPEC", "1.0", "spec", _json(spec_artifact)),
        "workflow.json": ("WF", "1.0", "workflow", _json(workflow_artifact)),
    }
    if changes is not None:
        files["changes.json"] = ("CHG", "1.0", "change_requests", _json(changes))
    if code is not None:
        files["router.py"] = ("PY", "1.0", "python", code)
    artifacts: list[dict[str, object]] = []
    for path, (artifact_id, version, kind, content) in files.items():
        entry: dict[str, object] = {
            "artifact_id": artifact_id,
            "version": version,
            "kind": kind,
            "path": path,
            "sha256": _digest(content),
        }
        if path == "router.py":
            entry["link"] = {
                "entrypoint": "route",
                "param": "ticket",
                "field_map": {"wait_time": {"field": "wait_time", "unit": "minutes"}},
                "destination_map": {},
            }
        artifacts.append(entry)
    review_target: dict[str, object] = {
        "requirements": ["REQ@1.0"],
        "spec": "SPEC@1.0",
        "workflow": "WF@1.0",
    }
    if code is not None:
        review_target["code"] = ["PY@1.0"]
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


def _write_label(name: str, expected: dict[str, object], rationale: str) -> Path:
    LABELS.mkdir(parents=True, exist_ok=True)
    path = LABELS / f"{name}.json"
    payload = {
        "bundle": name,
        "stage": 2,
        "holdout_id": "stage2_semantic_v1",
        "derivation": (
            "Hand-derived from the requirement list, the effective-policy rules and the "
            "change/approval records before any Stage 2 verifier existed. No implementation "
            "output was used to produce this label."
        ),
        "expected": expected,
        "rationale": rationale,
    }
    path.write_text(_json(payload), encoding="utf-8", newline="\n")
    return path


def _write_manifest(paths: list[Path]) -> Path:
    lines = []
    for path in sorted(paths):
        relative = path.relative_to(OUT).as_posix()
        lines.append(f"{_digest(path.read_text(encoding='utf-8'))}  {relative}")
    manifest = OUT / "MANIFEST.sha256"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return manifest


def _write_generation(assignment: dict[str, str], labels: list[Path]) -> Path:
    payload = {
        "holdout_id": "stage2_semantic_v1",
        "reserved_on": "2026-11-17",
        "generated_by": "scripts/build_stage2_holdout.py",
        "generator_sha256": _digest(Path(__file__).read_text(encoding="utf-8")),
        "seed": SEED,
        "seed_usage": (
            "random.Random(seed).sample assigns one distinct equivalent threshold formulation "
            "per bundle from the vetted phrase pool; no other randomness is used."
        ),
        "threshold_phrases": list(THRESHOLD_PHRASES),
        "phrase_assignment": assignment,
        "config": {
            "path": "configs/default.toml",
            "sha256": _digest((ROOT / "configs" / "default.toml").read_text(encoding="utf-8")),
        },
        "provenance": {
            "baseline_implementation_checkpoint": "04d9ea6",
            "baseline_meaning": (
                "Stage 1 implementation the Stage 2 fixtures are authored against; it is not "
                "the commit that generated these files."
            ),
            "fixture_generation_base_commit": "9a420cd",
            "fixture_generation_commit": (
                "The commit that first adds holdout_stage2/ and contains this file. Recorded "
                "in docs/evaluation_protocol.md because a commit cannot name its own hash."
            ),
        },
        "domain": "support-ticket routing (category, priority, wait_time, vip)",
        "bundles": list(BUNDLE_NAMES),
        "labels": sorted(path.stem for path in labels),
        "policy": (
            "Frozen before Stage 2 implementation. Not executed by ordinary development "
            "tests. Labels are never reviewer inputs and must not be edited after freezing."
        ),
    }
    target = OUT / "GENERATION.json"
    target.write_text(_json(payload), encoding="utf-8", newline="\n")
    return target


def main() -> int:
    # Deterministic fixture selection only; this value is never used for security.
    rng = random.Random(SEED)  # noqa: S311
    phrases = rng.sample(THRESHOLD_PHRASES, len(BUNDLE_NAMES))
    assignment = dict(zip(BUNDLE_NAMES, phrases, strict=True))

    paths: list[Path] = []
    label_paths: list[Path] = []

    paths += write_bundle(
        BUNDLES / "G01_valid_original",
        "G01_valid_original",
        requirements=requirements_text(assignment["G01_valid_original"]),
        spec_artifact=spec(30),
        workflow_artifact=workflow(30),
    )
    label_paths.append(
        _write_label(
            "G01_valid_original",
            {
                "status": "PASS",
                "finding_categories": [],
                "resolution": {"applied": [], "undetermined": [], "rule_thresholds": {"T3": "30"}},
                "routes": {
                    "missing_category": "manual_review",
                    "urgent": "priority_queue",
                    "waiting_30": "escalation_queue",
                    "waiting_29": "normal_queue",
                    "baseline": "normal_queue",
                },
                "counterexamples": [],
            },
            "Spec and workflow match the ordered requirements at the original 30-minute "
            "threshold; no change is pending.",
        )
    )

    paths += write_bundle(
        BUNDLES / "G02_valid_approved_25",
        "G02_valid_approved_25",
        requirements=requirements_text(assignment["G02_valid_approved_25"]),
        spec_artifact=spec(25),
        workflow_artifact=workflow(25),
        changes=THRESHOLD_CHANGE,
        approvals=[_approval("AP-G02", THRESHOLD_CHANGE, "approve", approver="riley")],
    )
    label_paths.append(
        _write_label(
            "G02_valid_approved_25",
            {
                "status": "PASS",
                "finding_categories": [],
                "resolution": {
                    "applied": ["C1@1"],
                    "undetermined": [],
                    "rule_thresholds": {"T3": "25"},
                },
                "routes": {
                    "missing_category": "manual_review",
                    "urgent": "priority_queue",
                    "waiting_25": "escalation_queue",
                    "waiting_24": "normal_queue",
                    "waiting_30": "escalation_queue",
                    "baseline": "normal_queue",
                },
                "counterexamples": [],
            },
            "The hash-bound approval lowers T3 to 25 minutes; spec and workflow both implement "
            "25, so the effective policy and the artifacts agree.",
        )
    )

    paths += write_bundle(
        BUNDLES / "G03_precedence_defect",
        "G03_precedence_defect",
        requirements=requirements_text(assignment["G03_precedence_defect"]),
        spec_artifact=spec(30, wait_first=True),
        workflow_artifact=workflow(30, wait_first=True),
    )
    label_paths.append(
        _write_label(
            "G03_precedence_defect",
            {
                "status": "CHANGES_REQUIRED",
                "finding_categories": ["PRECEDENCE"],
                "resolution": {"applied": [], "undetermined": [], "rule_thresholds": {"T3": "30"}},
                "routes": {
                    "missing_category": "manual_review",
                    "urgent": "priority_queue",
                    "waiting_30": "escalation_queue",
                    "baseline": "normal_queue",
                },
                "counterexamples": [
                    {
                        "ticket": {"priority": "normal", "wait_time": 45},
                        "expected_destination": "manual_review",
                        "actual_destination": "escalation_queue",
                        "artifacts": ["spec:SPEC@1.0", "workflow:WF@1.0"],
                    },
                    {
                        "ticket": {"category": "billing", "priority": "urgent", "wait_time": 45},
                        "expected_destination": "priority_queue",
                        "actual_destination": "escalation_queue",
                        "artifacts": ["spec:SPEC@1.0", "workflow:WF@1.0"],
                    },
                ],
            },
            "Each rule is individually equivalent to its requirement, but both artifacts "
            "evaluate the wait threshold before missing-category and urgent handling. Tickets "
            "that match two rules prove the combined precedence is wrong.",
        )
    )

    paths += write_bundle(
        BUNDLES / "G04_code_boundary_defect",
        "G04_code_boundary_defect",
        requirements=requirements_text(assignment["G04_code_boundary_defect"]),
        spec_artifact=spec(30),
        workflow_artifact=workflow(30),
        code=ROUTER_SOURCE,
    )
    label_paths.append(
        _write_label(
            "G04_code_boundary_defect",
            {
                "status": "CHANGES_REQUIRED",
                "finding_categories": ["THRESHOLD_CHANGED"],
                "resolution": {"applied": [], "undetermined": [], "rule_thresholds": {"T3": "30"}},
                "routes": {
                    "missing_category": "manual_review",
                    "urgent": "priority_queue",
                    "waiting_30": "escalation_queue",
                    "baseline": "normal_queue",
                },
                "counterexamples": [
                    {
                        "ticket": {"category": "billing", "priority": "normal", "wait_time": 30},
                        "expected_destination": "escalation_queue",
                        "actual_destination": "normal_queue",
                        "artifacts": ["code:PY@1.0"],
                    }
                ],
            },
            "Spec and workflow use wait_time >= 30, but the linked router uses > 30. The "
            "boundary ticket wait=30 exposes the off-by-one disagreement.",
        )
    )

    paths += write_bundle(
        BUNDLES / "G05_conflicting_approvals",
        "G05_conflicting_approvals",
        requirements=requirements_text(assignment["G05_conflicting_approvals"]),
        spec_artifact=spec(25),
        workflow_artifact=workflow(25),
        changes=THRESHOLD_CHANGE,
        approvals=[
            _approval("AP-G05-A", THRESHOLD_CHANGE, "approve", approver="riley"),
            _approval("AP-G05-B", THRESHOLD_CHANGE, "reject", approver="sam"),
        ],
    )
    label_paths.append(
        _write_label(
            "G05_conflicting_approvals",
            {
                "status": "INCOMPLETE",
                "finding_categories": ["UNDETERMINED_CONFLICT"],
                "resolution": {
                    "applied": [],
                    "undetermined": [{"rule": "T3", "scope": "approval"}],
                    "rule_thresholds": {"T3": "undetermined"},
                },
                "routes": {
                    "missing_category": "manual_review",
                    "urgent": "priority_queue",
                    "baseline": "normal_queue",
                },
                "undetermined_routes": ["waiting_25", "waiting_30"],
                "counterexamples": [],
            },
            "Approval and rejection bind to the same change hash with no supersession, so the "
            "effective threshold is undetermined. Artifacts use 25, but no verdict is possible "
            "until authority is resolved.",
        )
    )

    generation = _write_generation(assignment, label_paths)
    paths.extend(label_paths)
    paths.append(generation)
    manifest = _write_manifest(paths)

    for path in paths:
        print(path.relative_to(ROOT))
    print(manifest.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
