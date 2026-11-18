"""Write the post-remediation development bundles and case labels.

These fixtures are authored independently of the Stage 4 development set and of
the sealed cases. They exist to exercise approved-revision correctness, approval
conflicts and semantic diagnosis after remediation; they are not a holdout.

The builder writes bundle bytes, manifest hashes and expected labels. The labels
are reasoned independently (see ``independent_reasoning``) from the bundle
content, not from reviewer output.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from verityflow.models.changes import ChangeArtifact

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "remediation"
CASES = ROOT / "evaluation" / "remediation" / "cases"

DESTINATION_MAP = {
    "manual": "manual_review",
    "priority": "priority_queue",
    "escalation": "escalation_queue",
    "normal": "normal_queue",
}

REQUIREMENTS = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, waiting is at least 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
"""

EXCEPTION_REQUIREMENTS = """\
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
Exception to R2: if vip, route to manual review.
R3. Otherwise, waiting is at least 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
"""


def _dictionary(decimal: bool = False, scale: int | None = None) -> dict[str, object]:
    wait: dict[str, object] = {
        "name": "wait_time",
        "type": "decimal" if decimal else "integer",
        "unit": "minutes",
        "nullable": True,
    }
    if scale is not None:
        wait["scale"] = scale
    return {
        "fields": [
            {
                "name": "category",
                "type": "string",
                "nullable": True,
                "domain": ["billing", "technical", "general"],
            },
            {
                "name": "priority",
                "type": "string",
                "nullable": True,
                "domain": ["urgent", "normal"],
            },
            wait,
            {"name": "vip", "type": "boolean", "nullable": True},
        ],
        "destinations": [
            {"id": "manual_review", "aliases": ["manual review"]},
            {"id": "priority_queue", "aliases": ["priority queue"]},
            {"id": "escalation_queue", "aliases": ["escalation queue"]},
            {"id": "normal_queue", "aliases": ["normal queue"]},
        ],
        "aliases": {"waiting": "wait_time"},
        "named_predicates": {
            "urgent ticket": {"kind": "eq", "field": "priority", "value": "urgent"}
        },
    }


def _spec(
    threshold: int | float,
    *,
    op: str = ">=",
    urgent: bool = True,
    exception: bool = False,
) -> dict[str, object]:
    rules: list[dict[str, object]] = [
        {
            "id": "S1",
            "priority": 1,
            "when": {"kind": "missing", "field": "category"},
            "destination": "manual_review",
        }
    ]
    urgent_condition = {"kind": "eq", "field": "priority", "value": "urgent"}
    if exception:
        rules.append(
            {
                "id": "S2",
                "priority": 2,
                "when": {
                    "kind": "and",
                    "items": [urgent_condition, {"kind": "eq", "field": "vip", "value": True}],
                },
                "destination": "manual_review",
            }
        )
    if urgent:
        rules.append(
            {
                "id": "S3",
                "priority": 3,
                "when": urgent_condition,
                "destination": "priority_queue",
            }
        )
    rules.append(
        {
            "id": "S4",
            "priority": 4,
            "when": {
                "kind": "cmp",
                "field": "wait_time",
                "op": op,
                "value": threshold,
                "unit": "minutes",
            },
            "destination": "escalation_queue",
        }
    )
    return {
        "spec_id": "SPEC",
        "version": "1.0",
        "rules": rules,
        "default": {"destination": "normal_queue"},
    }


def _workflow(
    threshold: int | float,
    *,
    op: str = ">=",
    urgent: bool = True,
    exception: bool = False,
) -> dict[str, object]:
    branches: list[dict[str, object]] = [
        {
            "branch_id": "b1",
            "version": "1.0",
            "condition": {"kind": "missing", "field": "category"},
            "target": "manual",
        }
    ]
    urgent_condition = {"kind": "eq", "field": "priority", "value": "urgent"}
    if exception:
        branches.append(
            {
                "branch_id": "b2",
                "version": "1.0",
                "condition": {
                    "kind": "and",
                    "items": [urgent_condition, {"kind": "eq", "field": "vip", "value": True}],
                },
                "target": "manual",
            }
        )
    if urgent:
        branches.append(
            {
                "branch_id": "b3",
                "version": "1.0",
                "condition": urgent_condition,
                "target": "priority",
            }
        )
    branches.append(
        {
            "branch_id": "b4",
            "version": "1.0",
            "condition": {
                "kind": "cmp",
                "field": "wait_time",
                "op": op,
                "value": threshold,
                "unit": "minutes",
            },
            "target": "escalation",
        }
    )
    branches.append({"branch_id": "b5", "version": "1.0", "target": "normal"})
    return {
        "workflow_id": "WF",
        "version": "1.0",
        "start": "start",
        "nodes": [
            {"id": "start", "version": "1.0", "kind": "decision", "branches": branches},
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


def _router(
    threshold: int | float,
    *,
    op: str = ">=",
    reorder: bool = False,
    unguarded_category: bool = False,
) -> str:
    lines = ['"""Support-ticket router."""', "", ""]
    if unguarded_category:
        lines.append("def route(ticket):")
        lines.append('    category = ticket["category"]')
        lines.append('    if "priority" in ticket and ticket["priority"] == "urgent":')
        lines.append('        return "priority"')
        lines.append(f'    if "wait_time" in ticket and ticket["wait_time"] {op} {threshold}:')
        lines.append('        return "escalation"')
        lines.append('    return "normal"')
        return "\n".join(lines) + "\n"
    lines.append("def route(ticket):")
    if not reorder:
        lines.append('    if "category" not in ticket:')
        lines.append('        return "manual"')
    lines.append('    if "priority" in ticket and ticket["priority"] == "urgent":')
    lines.append('        return "priority"')
    if reorder:
        lines.append('    if "category" not in ticket:')
        lines.append('        return "manual"')
    lines.append(f'    if "wait_time" in ticket and ticket["wait_time"] {op} {threshold}:')
    lines.append('        return "escalation"')
    lines.append('    return "normal"')
    return "\n".join(lines) + "\n"


def _exception_router() -> str:
    return '''"""Support-ticket router with the urgent/vip exception."""


def route(ticket):
    if "category" not in ticket:
        return "manual"
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if (
        "priority" in ticket
        and ticket["priority"] == "urgent"
        and "vip" in ticket
        and ticket["vip"]
    ):
        return "manual"
    if "wait_time" in ticket and ticket["wait_time"] >= 30:
        return "escalation"
    return "normal"
'''


def _wrong_destination_router() -> str:
    return '''"""Support-ticket router returning the wrong urgent destination."""


def route(ticket):
    if "category" not in ticket:
        return "manual"
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "manual"
    if "wait_time" in ticket and ticket["wait_time"] >= 30:
        return "escalation"
    return "normal"
'''


def _change(
    change_id: str,
    *,
    target: str = "R3",
    from_value: int | float = 30,
    to_value: int | float = 25,
    from_unit: str = "minutes",
    to_unit: str = "minutes",
    sequence: int | None = 1,
    base_version: str = "1.0",
    operation: str = "replace_threshold",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "change_id": change_id,
        "change_version": "1",
        "base_artifact": "REQ",
        "base_version": base_version,
        "target_rule": target,
        "operation": operation,
        "from": {"value": from_value, "unit": from_unit},
        "to": {"value": to_value, "unit": to_unit},
        "sequence": sequence,
        "rationale": "Policy-owner change under remediation review.",
    }
    return payload


def _destination_change(
    change_id: str,
    *,
    target: str,
    from_destination: str,
    to_destination: str,
    sequence: int | None = 1,
) -> dict[str, object]:
    return {
        "change_id": change_id,
        "change_version": "1",
        "base_artifact": "REQ",
        "base_version": "1.0",
        "target_rule": target,
        "operation": "replace_destination",
        "from": {"destination": from_destination},
        "to": {"destination": to_destination},
        "sequence": sequence,
        "rationale": "Policy-owner change under remediation review.",
    }


def _changes(*items: dict[str, object]) -> dict[str, object]:
    return {"changes": list(items)}


def _hash(changes: dict[str, object], change_id: str, change_version: str = "1") -> str:
    artifact = ChangeArtifact.model_validate(changes)
    record = next(
        item
        for item in artifact.changes
        if item.change_id == change_id and item.change_version == change_version
    )
    return record.content_hash()


def _approval(
    approval_id: str,
    changes: dict[str, object],
    *,
    change_id: str = "C1",
    decision: str = "approve",
    supersedes: list[str] | None = None,
    bad_hash: bool = False,
) -> dict[str, object]:
    return {
        "approval_id": approval_id,
        "change_id": change_id,
        "change_version": "1",
        "change_sha256": "0" * 64 if bad_hash else _hash(changes, change_id),
        "decision": decision,
        "approver_id": "alice",
        "approver_role": "policy_owner",
        "recorded_at": "2026-03-01T10:00:00Z",
        "supersedes": supersedes or [],
    }


def _json(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_bundle(
    name: str,
    *,
    suite: str,
    requirements: str = REQUIREMENTS,
    dictionary: dict[str, object] | None = None,
    spec: dict[str, object] | None = None,
    workflow: dict[str, object] | None = None,
    changes: dict[str, object] | None = None,
    approvals: list[dict[str, object]] | None = None,
    router: str | None = None,
) -> list[Path]:
    root = FIXTURES / suite / name
    files: dict[str, tuple[str, str, str, str]] = {
        "dictionary.json": ("DICT", "1.0", "data_dictionary", _json(dictionary or _dictionary())),
        "requirements.txt": ("REQ", "1.0", "requirements", requirements),
        "spec.json": ("SPEC", "1.0", "spec", _json(spec or _spec(30))),
        "workflow.json": ("WF", "1.0", "workflow", _json(workflow or _workflow(30))),
    }
    if changes is not None:
        files["changes.json"] = ("CHG", "1.0", "change_requests", _json(changes))
    if router is not None:
        files["router.py"] = ("ROUTER", "1.0", "python", router)
    artifacts: list[dict[str, object]] = []
    for path, (artifact_id, version, kind, content) in files.items():
        entry: dict[str, object] = {
            "artifact_id": artifact_id,
            "version": version,
            "kind": kind,
            "path": path,
            "sha256": _digest(content),
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


_TICKETS_BASE = {
    "category=<absent>,priority=urgent,wait_time=<absent>,vip=<absent>": "manual_review",
    "category=billing,priority=urgent,wait_time=30,vip=False": "priority_queue",
    "category=billing,priority=normal,wait_time=25,vip=False": "normal_queue",
    "category=billing,priority=normal,wait_time=29,vip=False": "normal_queue",
    "category=billing,priority=normal,wait_time=30,vip=False": "escalation_queue",
    "category=<absent>,priority=normal,wait_time=60,vip=False": "manual_review",
}


def _tickets(**overrides: str) -> dict[str, str]:
    tickets = dict(_TICKETS_BASE)
    for key, value in overrides.items():
        tickets[key.replace("__", ",").replace("_eq_", "=")] = value
    return tickets


def _tickets_25() -> dict[str, str]:
    tickets = dict(_TICKETS_BASE)
    tickets["category=billing,priority=normal,wait_time=25,vip=False"] = "escalation_queue"
    return tickets


def write_case(
    name: str,
    *,
    suite: str,
    status: str,
    defects: list[str],
    primary: str,
    reasoning: str,
    tickets: dict[str, str],
    extra: dict[str, object] | None = None,
    scenarios: list[str] | None = None,
) -> Path:
    payload: dict[str, object] = {
        "bundle_id": name,
        "suite": "remediation-development",
        "bundle_path": f"fixtures/remediation/{suite}/{name}",
        "expected": {
            "status": status,
            "defect_classes": defects,
            "primary_defect": primary,
            "tickets": tickets,
        },
        "method": "independent reasoning from bundle bytes",
        "independent_reasoning": reasoning,
        "reviewer_output_used": False,
        "scenarios": scenarios or [],
    }
    if extra:
        payload["expected"].update(extra)  # type: ignore[union-attr]
    CASES.mkdir(parents=True, exist_ok=True)
    path = CASES / f"{name}.json"
    path.write_text(_json(payload), encoding="utf-8", newline="\n")
    return path


def build_approval_cases() -> None:
    suite = "approvals"
    change_25 = _changes(_change("C1"))
    approve_25 = [_approval("A1", change_25)]

    write_bundle("A_original_30", suite=suite, router=_router(30))
    write_case(
        "A_original_30",
        suite=suite,
        status="PASS",
        defects=[],
        primary="",
        reasoning=(
            "No change artifact and no approvals. Every artifact implements the base "
            "30-minute threshold, so routing agrees everywhere."
        ),
        tickets=_TICKETS_BASE,
        extra={"effective_threshold": 30},
        scenarios=["original policy"],
    )

    write_bundle(
        "B_approved_25_impl_25",
        suite=suite,
        spec=_spec(25),
        workflow=_workflow(25),
        changes=change_25,
        approvals=approve_25,
        router=_router(25),
    )
    write_case(
        "B_approved_25_impl_25",
        suite=suite,
        status="PASS",
        defects=[],
        primary="",
        reasoning=(
            "A1 is an authorized, hash-bound approval of C1; applying C1 once makes the "
            "effective threshold 25 minutes, which every artifact implements."
        ),
        tickets=_tickets_25(),
        extra={"effective_threshold": 25, "applied_changes": ["C1@1"]},
        scenarios=["approved threshold"],
    )

    write_bundle(
        "C_approved_25_impl_30",
        suite=suite,
        changes=change_25,
        approvals=approve_25,
        router=_router(30),
    )
    write_case(
        "C_approved_25_impl_30",
        suite=suite,
        status="CHANGES_REQUIRED",
        defects=["THRESHOLD_CHANGED"],
        primary="THRESHOLD_CHANGED",
        reasoning=(
            "The effective threshold is 25 after C1, but the spec, workflow and router "
            "still compare against 30; tickets waiting 25-29 minutes are misrouted."
        ),
        tickets=_tickets_25(),
        extra={"effective_threshold": 25, "applied_changes": ["C1@1"]},
        scenarios=["approved threshold", "implementation lag"],
    )

    write_bundle(
        "D_unapproved_25_impl_30",
        suite=suite,
        changes=change_25,
        router=_router(30),
    )
    write_case(
        "D_unapproved_25_impl_30",
        suite=suite,
        status="PASS",
        defects=[],
        primary="",
        reasoning=(
            "C1 has no active approval, so the effective threshold remains 30. The "
            "artifacts implement 30; the unapproved proposal is disclosed but not applied."
        ),
        tickets=_TICKETS_BASE,
        extra={"effective_threshold": 30, "applied_changes": []},
        scenarios=["unapproved change"],
    )

    stale = _changes(_change("C1", base_version="2.0"))
    write_bundle(
        "E_wrong_revision_binding",
        suite=suite,
        changes=stale,
        approvals=[_approval("A1", stale)],
        router=_router(30),
    )
    write_case(
        "E_wrong_revision_binding",
        suite=suite,
        status="INCOMPLETE",
        defects=["STALE_CHANGE"],
        primary="STALE_CHANGE",
        reasoning=(
            "C1 targets REQ@2.0 while the reviewed requirements are REQ@1.0, so the "
            "change cannot apply. The artifacts implement the base 30 threshold; the "
            "mismatch is disclosed as stale rather than as a behavioural defect."
        ),
        tickets=_TICKETS_BASE,
        extra={"effective_threshold": 30, "applied_changes": []},
        scenarios=["wrong revision binding"],
    )

    write_bundle(
        "F_wrong_operator",
        suite=suite,
        router=_router(30, op="<"),
    )
    write_case(
        "F_wrong_operator",
        suite=suite,
        status="CHANGES_REQUIRED",
        defects=["WRONG_OPERATOR"],
        primary="WRONG_OPERATOR",
        reasoning=(
            "The requirement is 'at least 30 minutes'. The spec and workflow implement "
            "'>= 30'; the linked router uses '< 30', which routes the complementary "
            "region. This is an operator-direction defect in the router."
        ),
        tickets={
            "category=billing,priority=normal,wait_time=30,vip=False": "escalation_queue",
            "category=billing,priority=normal,wait_time=10,vip=False": "normal_queue",
        },
        extra={"effective_threshold": 30},
        scenarios=["wrong operator"],
    )

    unit_change = _changes(
        _change("C1", from_value=30, to_value=1500, from_unit="minutes", to_unit="seconds")
    )
    write_bundle(
        "G_unit_conversion",
        suite=suite,
        spec=_spec(25),
        workflow=_workflow(25),
        changes=unit_change,
        approvals=[_approval("A1", unit_change)],
        router=_router(25),
    )
    write_case(
        "G_unit_conversion",
        suite=suite,
        status="PASS",
        defects=[],
        primary="",
        reasoning=(
            "C1 approves 1500 seconds, which normalizes to 25 minutes in the field unit; "
            "the artifacts compare in minutes at 25, so the change applies exactly once."
        ),
        tickets=_tickets_25(),
        extra={"effective_threshold": 25, "applied_changes": ["C1@1"]},
        scenarios=["unit normalization"],
    )

    sequential = _changes(
        _change("C1", from_value=30, to_value=28, sequence=1),
        _change("C2", from_value=28, to_value=24, sequence=2),
    )
    write_bundle(
        "H_sequential_changes",
        suite=suite,
        spec=_spec(24),
        workflow=_workflow(24),
        changes=sequential,
        approvals=[
            _approval("A1", sequential, change_id="C1"),
            _approval("A2", sequential, change_id="C2"),
        ],
        router=_router(24),
    )
    write_case(
        "H_sequential_changes",
        suite=suite,
        status="PASS",
        defects=[],
        primary="",
        reasoning=(
            "C1 lowers 30 to 28 and C2 lowers 28 to 24; applying both once in sequence "
            "gives 24, which the artifacts implement."
        ),
        tickets={
            **_TICKETS_BASE,
            "category=billing,priority=normal,wait_time=24,vip=False": "escalation_queue",
            "category=billing,priority=normal,wait_time=25,vip=False": "escalation_queue",
        },
        extra={"effective_threshold": 24, "applied_changes": ["C1@1", "C2@1"]},
        scenarios=["sequential approved changes"],
    )

    disputed = _changes(_change("C1"))
    write_bundle(
        "I_conflicting_disputed_impl",
        suite=suite,
        spec=_spec(25),
        workflow=_workflow(25),
        changes=disputed,
        approvals=[
            _approval("A1", disputed, decision="approve"),
            _approval("A2", disputed, decision="reject"),
        ],
        router=_router(25),
    )
    write_case(
        "I_conflicting_disputed_impl",
        suite=suite,
        status="INCOMPLETE",
        defects=["UNDETERMINED_CONFLICT"],
        primary="UNDETERMINED_CONFLICT",
        reasoning=(
            "A1 approves and A2 rejects the same hash-bound C1. The artifacts implement "
            "the disputed 25-minute threshold; because the base 30 policy is only one "
            "permitted interpretation, the difference depends on the unresolved approval "
            "and cannot be a confirmed blocker."
        ),
        tickets=_TICKETS_BASE,
        extra={"effective_threshold": 30},
        scenarios=["contradictory approvals", "disputed threshold implemented"],
    )

    write_bundle(
        "J_conflict_plus_independent_defect",
        suite=suite,
        spec=_spec(25),
        workflow=_workflow(25),
        changes=disputed,
        approvals=[
            _approval("A1", disputed, decision="approve"),
            _approval("A2", disputed, decision="reject"),
        ],
        router=_router(25, reorder=True),
    )
    write_case(
        "J_conflict_plus_independent_defect",
        suite=suite,
        status="CHANGES_REQUIRED",
        defects=["UNDETERMINED_CONFLICT", "PRECEDENCE"],
        primary="PRECEDENCE",
        reasoning=(
            "The threshold conflict is undetermined, but the router also tests urgency "
            "before the missing-category guard. Tickets with no category and urgent "
            "priority are routed to the priority queue instead of manual review under "
            "every permitted threshold interpretation, so an independent blocker remains."
        ),
        tickets={
            "category=<absent>,priority=urgent,wait_time=<absent>,vip=<absent>": "manual_review",
            "category=billing,priority=urgent,wait_time=<absent>,vip=False": "priority_queue",
            "category=billing,priority=normal,wait_time=30,vip=False": "escalation_queue",
        },
        extra={"effective_threshold": 30},
        scenarios=["contradictory approvals", "independent precedence defect"],
    )

    multiple = _changes(
        _change("C1"),
        _destination_change(
            "C2",
            target="R2",
            from_destination="priority_queue",
            to_destination="manual_review",
        ),
    )
    write_bundle(
        "K_multiple_conflicts",
        suite=suite,
        changes=multiple,
        approvals=[
            _approval("A1", multiple, change_id="C1", decision="approve"),
            _approval("A2", multiple, change_id="C1", decision="reject"),
            _approval("A3", multiple, change_id="C2", decision="approve"),
            _approval("A4", multiple, change_id="C2", decision="reject"),
        ],
        router=_router(30),
    )
    write_case(
        "K_multiple_conflicts",
        suite=suite,
        status="INCOMPLETE",
        defects=["UNDETERMINED_CONFLICT"],
        primary="UNDETERMINED_CONFLICT",
        reasoning=(
            "C1 (threshold) and C2 (urgent destination) each have one approval and one "
            "rejection. The confirmed base policy is 30/priority. The artifacts implement "
            "that base, but the differences under each diagnostic variant are unresolved, "
            "so both scopes are disclosed as undetermined."
        ),
        tickets=_TICKETS_BASE,
        extra={"effective_threshold": 30},
        scenarios=["two conflicts on different rules"],
    )

    write_bundle(
        "L_valid_supersession",
        suite=suite,
        changes=change_25,
        approvals=[
            _approval("A1", change_25, decision="approve"),
            _approval("A2", change_25, decision="reject", supersedes=["A1"]),
        ],
        router=_router(30),
    )
    write_case(
        "L_valid_supersession",
        suite=suite,
        status="PASS",
        defects=[],
        primary="",
        reasoning=(
            "A2 is a valid later rejection that supersedes A1. The active decision is "
            "reject, C1 is not applied, and the artifacts implement the base 30 policy."
        ),
        tickets=_TICKETS_BASE,
        extra={"effective_threshold": 30, "applied_changes": []},
        scenarios=["valid approval supersession"],
    )

    write_bundle(
        "M_invalid_approval_hash",
        suite=suite,
        changes=change_25,
        approvals=[_approval("A1", change_25, bad_hash=True)],
        router=_router(30),
    )
    write_case(
        "M_invalid_approval_hash",
        suite=suite,
        status="INCOMPLETE",
        defects=["INVALID_APPROVAL"],
        primary="INVALID_APPROVAL",
        reasoning=(
            "A1 is bound to a different content hash than C1, so it grants no authority. "
            "C1 is unapproved, the effective threshold stays 30, and the authority "
            "failure is disclosed rather than treated as approval."
        ),
        tickets=_TICKETS_BASE,
        extra={"effective_threshold": 30, "applied_changes": []},
        scenarios=["invalid approval binding"],
    )

    write_bundle(
        "N_conflict_plus_runtime_error",
        suite=suite,
        spec=_spec(25),
        workflow=_workflow(25),
        changes=disputed,
        approvals=[
            _approval("A1", disputed, decision="approve"),
            _approval("A2", disputed, decision="reject"),
        ],
        router=_router(25, unguarded_category=True),
    )
    write_case(
        "N_conflict_plus_runtime_error",
        suite=suite,
        status="CHANGES_REQUIRED",
        defects=["UNDETERMINED_CONFLICT", "RUNTIME_ERROR"],
        primary="RUNTIME_ERROR",
        reasoning=(
            "The threshold is disputed, but the router dereferences a missing category "
            "without a guard. That raises on every permitted threshold interpretation, "
            "so the runtime defect is independent of the unresolved approval."
        ),
        tickets={
            "category=<absent>,priority=urgent,wait_time=<absent>,vip=<absent>": "manual_review",
        },
        extra={"effective_threshold": 30},
        scenarios=["contradictory approvals", "independent runtime error"],
    )


def build_semantic_cases() -> None:
    suite = "semantic"

    write_bundle("S1_integer_boundary", suite=suite, router=_router(30, op=">"))
    write_case(
        "S1_integer_boundary",
        suite=suite,
        status="CHANGES_REQUIRED",
        defects=["THRESHOLD_CHANGED"],
        primary="THRESHOLD_CHANGED",
        reasoning=(
            "The requirement fires at exactly 30 minutes; the router uses '> 30'. The "
            "declared integer grid makes the differing region exactly {30}."
        ),
        tickets={
            "category=billing,priority=normal,wait_time=30,vip=False": "escalation_queue",
            "category=billing,priority=normal,wait_time=31,vip=False": "escalation_queue",
        },
        scenarios=["integer boundary"],
    )

    write_bundle(
        "S2_decimal_boundary",
        suite=suite,
        dictionary=_dictionary(decimal=True, scale=1),
        spec=_spec(30),
        workflow=_workflow(30),
        router=_router(29.5),
    )
    write_case(
        "S2_decimal_boundary",
        suite=suite,
        status="CHANGES_REQUIRED",
        defects=["THRESHOLD_CHANGED"],
        primary="THRESHOLD_CHANGED",
        reasoning=(
            "With a 0.1-minute grid the router fires at 29.5 while the requirement fires "
            "at 30; representable tickets in [29.5, 30) differ."
        ),
        tickets={
            "category=billing,priority=normal,wait_time=29.5,vip=False": "escalation_queue",
            "category=billing,priority=normal,wait_time=30,vip=False": "escalation_queue",
        },
        scenarios=["decimal boundary"],
    )

    write_bundle(
        "S3_continuous_boundary",
        suite=suite,
        requirements=REQUIREMENTS.replace("at least 30 minutes", "more than 30 minutes"),
        dictionary=_dictionary(decimal=True),
        spec=_spec(30, op=">"),
        workflow=_workflow(30, op=">"),
        router=_router(30, op=">="),
    )
    write_case(
        "S3_continuous_boundary",
        suite=suite,
        status="CHANGES_REQUIRED",
        defects=["THRESHOLD_CHANGED"],
        primary="THRESHOLD_CHANGED",
        reasoning=(
            "On a continuous domain '> 30' and '>= 30' differ only at 30; the router "
            "fires where the requirement does not."
        ),
        tickets={
            "category=billing,priority=normal,wait_time=30,vip=False": "normal_queue",
            "category=billing,priority=normal,wait_time=30.5,vip=False": "escalation_queue",
        },
        scenarios=["continuous domain"],
    )

    write_bundle(
        "S4_missing_value_precedence",
        suite=suite,
        router=_router(30, reorder=True),
    )
    write_case(
        "S4_missing_value_precedence",
        suite=suite,
        status="CHANGES_REQUIRED",
        defects=["PRECEDENCE"],
        primary="PRECEDENCE",
        reasoning=(
            "The router checks urgency before the missing-category guard, so a ticket "
            "with no category and urgent priority leaves the manual-review region."
        ),
        tickets={
            "category=<absent>,priority=urgent,wait_time=<absent>,vip=<absent>": "manual_review",
            "category=<absent>,priority=normal,wait_time=30,vip=False": "manual_review",
        },
        scenarios=["missing-value precedence"],
    )

    write_bundle(
        "S5_exception_overlap",
        suite=suite,
        requirements=EXCEPTION_REQUIREMENTS,
        spec=_spec(30, exception=True),
        workflow=_workflow(30, exception=True),
        router=_exception_router(),
    )
    write_case(
        "S5_exception_overlap",
        suite=suite,
        status="CHANGES_REQUIRED",
        defects=["PRECEDENCE"],
        primary="PRECEDENCE",
        reasoning=(
            "The urgent+vip exception must be tested before the plain urgent rule; the "
            "router tests the plain rule first, so the exception is unreachable."
        ),
        tickets={
            "category=billing,priority=urgent,wait_time=<absent>,vip=True": "manual_review",
            "category=billing,priority=urgent,wait_time=<absent>,vip=False": "priority_queue",
        },
        scenarios=["overlapping urgency and exception"],
    )

    write_bundle(
        "S6_wrong_destination",
        suite=suite,
        spec=_spec(30),
        workflow=_workflow(30),
        router=_wrong_destination_router(),
    )
    write_case(
        "S6_wrong_destination",
        suite=suite,
        status="CHANGES_REQUIRED",
        defects=["WRONG_DESTINATION"],
        primary="WRONG_DESTINATION",
        reasoning=(
            "The urgent condition is equivalent to the requirement, but the router sends "
            "it to manual review instead of the priority queue."
        ),
        tickets={
            "category=billing,priority=urgent,wait_time=<absent>,vip=False": "priority_queue",
        },
        scenarios=["destination disagreement"],
    )

    write_bundle(
        "S7_runtime_error",
        suite=suite,
        spec=_spec(30),
        workflow=_workflow(30),
        router=_router(30, unguarded_category=True),
    )
    write_case(
        "S7_runtime_error",
        suite=suite,
        status="CHANGES_REQUIRED",
        defects=["RUNTIME_ERROR"],
        primary="RUNTIME_ERROR",
        reasoning=(
            "The router dereferences ticket['category'] without a presence guard, so a "
            "ticket with no category raises KeyError while the requirements route it to "
            "manual review."
        ),
        tickets={
            "category=<absent>,priority=urgent,wait_time=<absent>,vip=<absent>": "manual_review",
        },
        scenarios=["runtime error"],
    )

    write_bundle(
        "S8_overlapping_numeric_rules",
        suite=suite,
        requirements=REQUIREMENTS
        + ("Exception to R3: if waiting is at least 60 minutes, route to manual review.\n"),
        spec=_spec(30),
        workflow=_workflow(30),
        router=_router(30),
    )
    write_case(
        "S8_overlapping_numeric_rules",
        suite=suite,
        status="CHANGES_REQUIRED",
        defects=["MISSING_EXCEPTION"],
        primary="MISSING_EXCEPTION",
        reasoning=(
            "Two numeric rules apply at 60+ minutes and the exception narrows R3 to "
            "manual review; none of the artifacts implements the exception."
        ),
        tickets={
            "category=billing,priority=normal,wait_time=60,vip=False": "manual_review",
            "category=billing,priority=normal,wait_time=30,vip=False": "escalation_queue",
        },
        scenarios=["multiple applicable rules"],
    )


def main() -> int:
    build_approval_cases()
    build_semantic_cases()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
