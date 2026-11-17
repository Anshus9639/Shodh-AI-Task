"""Write the Stage 2 development bundles (B10-B14).

These bundles reuse the Stage 1 support-ticket domain and the frozen Stage 1
requirements/spec/workflow, then add one linked Python router each so behavioural
verification has development fixtures to tune against:

* B10_router_clean       - the router implements the requirements faithfully;
* B11_router_precedence  - two guards are plausible but in the wrong order;
* B12_router_threshold   - a strict boundary where the requirement is inclusive;
* B13_router_runtime     - an optional field is read without a presence guard;
* B14_router_unsupported - a strict boundary plus a branch returning an
                           undeclared destination, so part of the router is
                           unreviewed while an evaluated blocker is still found.

Only ``fixtures/dev`` is written. The sealed Stage 1 (``holdout``) and Stage 2
(``holdout_stage2``) sets are never opened or regenerated here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from build_fixtures import DICTIONARY, REQUIREMENTS, SPEC, WORKFLOW

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "fixtures" / "dev"

DESTINATION_MAP = {
    "manual": "manual_review",
    "priority": "priority_queue",
    "escalation": "escalation_queue",
    "normal": "normal_queue",
}

CLEAN = '''\
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

PRECEDENCE = '''\
"""Router for the support-ticket workflow."""


def route(ticket):
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if "category" not in ticket:
        return "manual"
    if "wait_time" in ticket and ticket["wait_time"] >= 30:
        return "escalation"
    return "normal"
'''

THRESHOLD = '''\
"""Router for the support-ticket workflow."""


def route(ticket):
    if "category" not in ticket:
        return "manual"
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if "wait_time" in ticket and ticket["wait_time"] > 30:
        return "escalation"
    return "normal"
'''

RUNTIME = '''\
"""Router for the support-ticket workflow."""


def route(ticket):
    if "category" not in ticket and ticket["vip"]:
        return "manual"
    if "category" not in ticket:
        return "manual"
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if "wait_time" in ticket and ticket["wait_time"] >= 30:
        return "escalation"
    return "normal"
'''

UNSUPPORTED = '''\
"""Router for the support-ticket workflow."""


def route(ticket):
    if "category" not in ticket:
        return "manual"
    if "priority" in ticket and ticket["priority"] == "urgent":
        return "priority"
    if "wait_time" in ticket and ticket["wait_time"] > 30:
        return "escalation"
    if "category" in ticket and ticket["category"] == "technical":
        return "legacy"
    return "normal"
'''


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_bundle(root: Path, bundle_id: str, source: str) -> list[Path]:
    files: dict[str, tuple[str, str, str, str]] = {
        "dictionary.json": ("DICT", "1.0", "data_dictionary", _json(DICTIONARY)),
        "requirements.txt": ("REQ", "1.0", "requirements", REQUIREMENTS),
        "spec.json": ("SPEC", "1.0", "spec", _json(SPEC)),
        "workflow.json": ("WF", "1.0", "workflow", _json(WORKFLOW)),
        "router.py": ("ROUTER", "1.0", "python", source),
    }
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
    manifest = {
        "bundle_id": bundle_id,
        "bundle_version": "1.0",
        "review_target": {
            "requirements": ["REQ@1.0"],
            "spec": "SPEC@1.0",
            "workflow": "WF@1.0",
            "code": ["ROUTER@1.0"],
        },
        "artifacts": artifacts,
        "approvals": [],
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
    bundles = {
        "B10_router_clean": CLEAN,
        "B11_router_precedence": PRECEDENCE,
        "B12_router_threshold": THRESHOLD,
        "B13_router_runtime": RUNTIME,
        "B14_router_unsupported": UNSUPPORTED,
    }
    for name, source in bundles.items():
        for path in write_bundle(DEV / name, name, source):
            print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
