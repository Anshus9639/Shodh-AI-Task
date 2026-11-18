"""Write the Stage 4 evaluation bundles (E01-E07).

These are development evaluation fixtures, not modifications of the sealed
Stage 1 (``holdout/``) or Stage 2 (``holdout_stage2/``) sets. They add scenarios
that the existing development bundles do not represent: an accepted approved
threshold change, a missing branch, a wrong destination, an interacting
exception, unsupported requirement language, unsupported Python syntax and a
missing-evidence/provenance failure.

Every expected label is authored separately in ``evaluation/cases``; this
generator writes bundle bytes and hashes only.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from build_fixtures import DICTIONARY, REQUIREMENTS, SPEC, WORKFLOW

from verityflow.models.changes import ChangeArtifact

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "evaluation"
BUNDLES = EVAL / "bundles"

DESTINATION_MAP = {
    "manual": "manual_review",
    "priority": "priority_queue",
    "escalation": "escalation_queue",
    "normal": "normal_queue",
}

CHANGE_25 = {
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

EXCEPTION_REQUIREMENTS = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, waiting >= 30 minutes -> escalation queue.
Exception to R3: if vip, route to priority queue.
R4. All remaining tickets -> normal queue.
"""

UNSUPPORTED_REQUIREMENTS = REQUIREMENTS + (
    "Refunds must be routed to the finance team by a senior reviewer.\n"
)

UNUSUAL_REQUIREMENTS = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, not waiting < 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
"""

PARAPHRASE_REQUIREMENTS = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, waiting is at least 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
"""

CLEAN_ROUTER = '''\
"""Router for the support-ticket workflow."""


def route(ticket):
    if "category" not in ticket:
        return "manual"
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if "wait_time" in ticket and ticket["wait_time"] >= 30:
        return "escalation"
    return "normal"
'''

UNSUPPORTED_ROUTER = '''\
"""Router for the support-ticket workflow."""


def route(ticket):
    for key in ticket:
        pass
    if "category" not in ticket:
        return "manual"
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if "wait_time" in ticket and ticket["wait_time"] >= 30:
        return "escalation"
    return "normal"
'''


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clone(value: dict[str, object]) -> dict[str, object]:
    return copy.deepcopy(value)


def _spec_with_threshold(value: int) -> dict[str, object]:
    spec = _clone(SPEC)
    rules = spec["rules"]
    assert isinstance(rules, list)
    for rule in rules:
        if rule["id"] == "S3":
            rule["when"]["value"] = value
    return spec


def _workflow_with_threshold(value: int) -> dict[str, object]:
    workflow = _clone(WORKFLOW)
    nodes = workflow["nodes"]
    assert isinstance(nodes, list)
    for node in nodes:
        if node["id"] != "start":
            continue
        for branch in node["branches"]:
            if branch["branch_id"] == "b3":
                branch["condition"]["value"] = value
    return workflow


def _without_urgent(spec: bool) -> dict[str, object]:
    value = _clone(SPEC if spec else WORKFLOW)
    if spec:
        rules = value["rules"]
        assert isinstance(rules, list)
        value["rules"] = [rule for rule in rules if rule["id"] != "S2"]
    else:
        nodes = value["nodes"]
        assert isinstance(nodes, list)
        start = next(node for node in nodes if node["id"] == "start")
        start["branches"] = [branch for branch in start["branches"] if branch["branch_id"] != "b2"]
        value["nodes"] = [node for node in nodes if node["id"] != "priority"]
    return value


def _urgent_to_normal(spec: bool) -> dict[str, object]:
    value = _clone(SPEC if spec else WORKFLOW)
    if spec:
        rules = value["rules"]
        assert isinstance(rules, list)
        for rule in rules:
            if rule["id"] == "S2":
                rule["destination"] = "normal_queue"
    else:
        nodes = value["nodes"]
        assert isinstance(nodes, list)
        for node in nodes:
            if node["id"] == "priority":
                node["destination"] = "normal_queue"
    return value


def _reordered_spec() -> dict[str, object]:
    spec = _clone(SPEC)
    rules = spec["rules"]
    assert isinstance(rules, list)
    spec["rules"] = list(reversed(rules))
    return spec


def _reordered_workflow() -> dict[str, object]:
    workflow = _clone(WORKFLOW)
    nodes = workflow["nodes"]
    assert isinstance(nodes, list)
    start = next(node for node in nodes if node["id"] == "start")
    terminals = [node for node in nodes if node["id"] != "start"]
    workflow["nodes"] = [start, *reversed(terminals)]
    return workflow


def _exception_spec() -> dict[str, object]:
    return {
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
            {
                "id": "S4",
                "priority": 4,
                "when": {"kind": "eq", "field": "vip", "value": True},
                "destination": "priority_queue",
            },
        ],
        "default": {"destination": "normal_queue"},
    }


def _exception_workflow() -> dict[str, object]:
    return {
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
                    {
                        "branch_id": "b4",
                        "version": "1.0",
                        "condition": {"kind": "eq", "field": "vip", "value": True},
                        "target": "priority",
                    },
                    {"branch_id": "b5", "version": "1.0", "target": "normal"},
                ],
            },
            {"id": "manual", "version": "1.0", "kind": "terminal", "destination": "manual_review"},
            {
                "id": "priority",
                "version": "1.0",
                "kind": "terminal",
                "destination": "priority_queue",
            },
            {
                "id": "escalation",
                "version": "1.0",
                "kind": "terminal",
                "destination": "escalation_queue",
            },
            {"id": "normal", "version": "1.0", "kind": "terminal", "destination": "normal_queue"},
        ],
    }


def _change_hash(changes: dict[str, object]) -> str:
    artifact = ChangeArtifact.model_validate(changes)
    return artifact.changes[0].content_hash()


def _approval(approval_id: str, changes: dict[str, object]) -> dict[str, object]:
    return {
        "approval_id": approval_id,
        "change_id": "C1",
        "change_version": "1",
        "change_sha256": _change_hash(changes),
        "decision": "approve",
        "approver_id": "alice",
        "approver_role": "policy_owner",
        "recorded_at": "2026-02-01T10:00:00Z",
        "supersedes": [],
    }


def write_bundle(
    name: str,
    *,
    requirements: str = REQUIREMENTS,
    spec: dict[str, object] | None = None,
    workflow: dict[str, object] | None = None,
    changes: dict[str, object] | None = None,
    approvals: list[dict[str, object]] | None = None,
    router: str | None = None,
    corrupt_spec: bool = False,
) -> list[Path]:
    root = BUNDLES / name
    files: dict[str, tuple[str, str, str, str]] = {
        "dictionary.json": ("DICT", "1.0", "data_dictionary", _json(DICTIONARY)),
        "requirements.txt": ("REQ", "1.0", "requirements", requirements),
        "spec.json": ("SPEC", "1.0", "spec", _json(spec or SPEC)),
        "workflow.json": ("WF", "1.0", "workflow", _json(workflow or WORKFLOW)),
    }
    if changes is not None:
        files["changes.json"] = ("CHG", "1.0", "change_requests", _json(changes))
    if router is not None:
        files["router.py"] = ("ROUTER", "1.0", "python", router)
    artifacts: list[dict[str, object]] = []
    for path, (artifact_id, version, kind, content) in files.items():
        digest = "0" * 64 if (corrupt_spec and path == "spec.json") else _digest(content)
        entry: dict[str, object] = {
            "artifact_id": artifact_id,
            "version": version,
            "kind": kind,
            "path": path,
            "sha256": digest,
        }
        if kind == "python":
            entry["link"] = {
                "entrypoint": "route",
                "param": "ticket",
                "field_map": {},
                "destination_map": DESTINATION_MAP,
            }
        artifacts.append(entry)
    review_target: dict[str, object] = {
        "requirements": ["REQ@1.0"],
        "spec": "SPEC@1.0",
        "workflow": "WF@1.0",
    }
    if router is not None:
        review_target["code"] = ["ROUTER@1.0"]
    manifest = {
        "bundle_id": name,
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


def main() -> int:
    bundles: dict[str, list[Path]] = {}
    bundles["E01_valid_approved_25"] = write_bundle(
        "E01_valid_approved_25",
        spec=_spec_with_threshold(25),
        workflow=_workflow_with_threshold(25),
        changes=CHANGE_25,
        approvals=[_approval("E1", CHANGE_25)],
    )
    bundles["E02_missing_branch"] = write_bundle(
        "E02_missing_branch",
        spec=_without_urgent(spec=True),
        workflow=_without_urgent(spec=False),
    )
    bundles["E03_wrong_destination"] = write_bundle(
        "E03_wrong_destination",
        spec=_urgent_to_normal(spec=True),
        workflow=_urgent_to_normal(spec=False),
    )
    bundles["E04_interacting_exceptions"] = write_bundle(
        "E04_interacting_exceptions",
        requirements=EXCEPTION_REQUIREMENTS,
        spec=_exception_spec(),
        workflow=_exception_workflow(),
    )
    bundles["E05_unsupported_language"] = write_bundle(
        "E05_unsupported_language",
        requirements=UNSUPPORTED_REQUIREMENTS,
    )
    bundles["E06_unsupported_python"] = write_bundle(
        "E06_unsupported_python",
        router=UNSUPPORTED_ROUTER,
    )
    bundles["E07_missing_evidence"] = write_bundle(
        "E07_missing_evidence",
        corrupt_spec=True,
    )
    bundles["E08_unusual_formulation"] = write_bundle(
        "E08_unusual_formulation",
        requirements=UNUSUAL_REQUIREMENTS,
    )
    bundles["E09_reordered_records"] = write_bundle(
        "E09_reordered_records",
        spec=_reordered_spec(),
        workflow=_reordered_workflow(),
    )
    bundles["E10_paraphrased_wording"] = write_bundle(
        "E10_paraphrased_wording",
        requirements=PARAPHRASE_REQUIREMENTS,
    )
    for name, paths in bundles.items():
        print(f"{name}: {len(paths)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
