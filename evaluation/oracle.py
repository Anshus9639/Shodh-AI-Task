"""Independent routing oracle for the Stage 4 evaluation.

This module deliberately imports nothing from ``verityflow``. It re-derives the
effective policy, approval resolution, per-ticket destinations and expected
review status from bundle bytes with its own parser and evaluators, so expected
labels cannot be produced by the implementation under test.

Scope: the support-ticket grammar used by the development and Stage 2 fixtures
(``category``, ``priority``, ``wait_time``, ``vip``). Anything outside that
grammar is reported as unsupported instead of being guessed.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_NUMERIC = re.compile(
    r"^(?P<field>[a-z_][a-z0-9_]*)\s*(?P<op>>=|<=|>|<|==|!=)\s*(?P<value>-?\d+)"
    r"(?:\s+minutes?)?$"
)
_NUMERIC_WORDS = re.compile(
    r"^(?P<field>[a-z_][a-z0-9_]*)\s+is\s+(?P<phrase>at\s+least|at\s+most|"
    r"greater\s+than\s+or\s+equal\s+to|less\s+than\s+or\s+equal\s+to|"
    r"greater\s+than|less\s+than)\s+(?P<value>-?\d+)(?:\s+minutes?)?$"
)
_WORD_OPS = {
    "at least": ">=",
    "at most": "<=",
    "greater than or equal to": ">=",
    "less than or equal to": "<=",
    "greater than": ">",
    "less than": "<",
}

TRUE = "true"
FALSE = "false"
UNKNOWN = "unknown"

MISSING = "<absent>"


class OracleUnsupported(ValueError):
    """The oracle's deliberately small grammar does not cover this input."""


@dataclass(frozen=True, slots=True)
class Rule:
    ref: str
    condition: tuple[object, ...]
    destination: str
    exception_of: str | None = None


@dataclass(frozen=True, slots=True)
class Element:
    element_id: str
    condition: tuple[object, ...]
    destination: str
    order: int
    exception_of: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactDiff:
    ticket: Mapping[str, object]
    expected: str | None
    actual: str | None


@dataclass(frozen=True, slots=True)
class ArtifactReview:
    name: str
    supported: bool
    differences: tuple[ArtifactDiff, ...] = ()
    runtime_errors: tuple[ArtifactDiff, ...] = ()
    unsupported_destinations: tuple[ArtifactDiff, ...] = ()
    coverage: tuple[str, ...] = ()
    detail: str = ""


@dataclass
class OracleResult:
    bundle_id: str
    status: str
    defect_classes: tuple[str, ...]
    primary_reason: str | None
    effective: dict[str, object]
    tickets: dict[str, str]
    artifacts: dict[str, ArtifactReview]
    notes: tuple[str, ...] = ()
    manual_checks: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return {
            "bundle_id": self.bundle_id,
            "status": self.status,
            "defect_classes": list(self.defect_classes),
            "primary_reason": self.primary_reason,
            "effective": self.effective,
            "tickets": self.tickets,
            "artifacts": {
                name: {
                    "supported": review.supported,
                    "differences": len(review.differences),
                    "runtime_errors": len(review.runtime_errors),
                    "unsupported_destinations": len(review.unsupported_destinations),
                    "coverage": list(review.coverage),
                    "detail": review.detail,
                }
                for name, review in self.artifacts.items()
            },
            "notes": list(self.notes),
            "manual_checks": list(self.manual_checks),
        }


# --------------------------------------------------------------------------- #
# Conditions
# --------------------------------------------------------------------------- #


def truth_and(left: str, right: str) -> str:
    if left == FALSE or right == FALSE:
        return FALSE
    if left == TRUE and right == TRUE:
        return TRUE
    return UNKNOWN


def truth_or(left: str, right: str) -> str:
    if left == TRUE or right == TRUE:
        return TRUE
    if left == FALSE and right == FALSE:
        return FALSE
    return UNKNOWN


def truth_not(value: str) -> str:
    if value == TRUE:
        return FALSE
    if value == FALSE:
        return TRUE
    return UNKNOWN


def evaluate(condition: tuple[object, ...], ticket: Mapping[str, object]) -> str:
    kind = condition[0]
    if kind == "true":
        return TRUE
    if kind == "and":
        return truth_and(evaluate(condition[1], ticket), evaluate(condition[2], ticket))
    if kind == "or":
        return truth_or(evaluate(condition[1], ticket), evaluate(condition[2], ticket))
    if kind == "not":
        return truth_not(evaluate(condition[1], ticket))
    field_name = str(condition[1])
    if field_name not in ticket:
        return TRUE if kind == "missing" else UNKNOWN
    value = ticket[field_name]
    if kind == "missing":
        return FALSE
    if kind == "eq":
        return TRUE if value == condition[2] else FALSE
    if kind == "cmp":
        op = str(condition[2])
        limit = condition[3]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise OracleUnsupported(f"non-numeric value for {field_name!r}")
        return TRUE if _compare(value, op, limit) else FALSE  # type: ignore[arg-type]
    raise OracleUnsupported(f"unknown condition {kind!r}")


def _compare(value: float, op: str, limit: object) -> bool:
    if not isinstance(limit, (int, float)):
        raise OracleUnsupported("non-numeric limit")
    if op == "<":
        return value < limit
    if op == "<=":
        return value <= limit
    if op == ">":
        return value > limit
    if op == ">=":
        return value >= limit
    if op == "==":
        return value == limit
    if op == "!=":
        return value != limit
    raise OracleUnsupported(f"unknown operator {op!r}")


# --------------------------------------------------------------------------- #
# Bundle loading
# --------------------------------------------------------------------------- #


@dataclass
class Bundle:
    path: Path
    manifest: dict[str, object]
    artifacts: dict[str, bytes]
    provenance_errors: list[str]

    @property
    def bundle_id(self) -> str:
        return str(self.manifest["bundle_id"])


def load_bundle(path: Path) -> Bundle:
    manifest_path = path / "bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts: dict[str, bytes] = {}
    errors: list[str] = []
    for entry in manifest.get("artifacts", []):
        rel = str(entry["path"])
        target = path / rel
        if not target.is_file():
            errors.append(f"{rel}: missing")
            continue
        raw = target.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != entry.get("sha256"):
            errors.append(f"{rel}: sha256 mismatch")
            continue
        artifacts[str(entry["artifact_id"])] = raw
    return Bundle(path=path, manifest=manifest, artifacts=artifacts, provenance_errors=errors)


def _text(bundle: Bundle, artifact_id: str) -> str:
    return bundle.artifacts[artifact_id].decode("utf-8")


def _json(bundle: Bundle, artifact_id: str) -> dict[str, object]:
    return json.loads(_text(bundle, artifact_id))


def _artifact_entry(bundle: Bundle, artifact_id: str) -> dict[str, object] | None:
    for entry in bundle.manifest.get("artifacts", []):
        if entry["artifact_id"] == artifact_id:
            return entry
    return None


# --------------------------------------------------------------------------- #
# Requirement parsing
# --------------------------------------------------------------------------- #


@dataclass
class ParsedRequirements:
    rules: tuple[Rule, ...]
    unsupported: tuple[str, ...]
    evidence: dict[str, str]


def _split_rule(line: str) -> tuple[str, str] | None:
    if "." not in line:
        return None
    ref, body = line.split(".", 1)
    if not ref or not ref[0].isalpha() or not all(c.isalnum() or c in "_.-" for c in ref):
        return None
    if not body.strip():
        return None
    return ref, body.strip()


def _strip_period(text: str) -> str:
    return text[:-1] if text.endswith(".") else text


def _destination(text: str, aliases: Mapping[str, str]) -> str:
    key = text.strip().rstrip(".")
    if key.lower().startswith("the "):
        key = key[4:]
    resolved = aliases.get(key.casefold()) or aliases.get(key.casefold().replace("_", " "))
    if resolved is None:
        raise OracleUnsupported(f"unknown destination {text!r}")
    return resolved


def _condition(body: str, dictionary: Mapping[str, object]) -> tuple[object, ...]:
    lowered = body.strip().casefold()
    lowered = lowered.removeprefix("otherwise,").strip()
    if lowered.startswith("if "):
        lowered = lowered[3:].strip()
    if lowered.startswith("not "):
        return ("not", _condition(lowered[4:], dictionary))
    if lowered in {"all remaining tickets", "all remaining", "everything else"}:
        return ("true",)
    named = dictionary.get("named_predicates", {})
    assert isinstance(named, dict)
    if lowered in named:
        predicate = named[lowered]
        return _json_condition(predicate, dictionary)
    missing = lowered.removeprefix("missing ")
    if missing != lowered:
        return ("missing", _field(missing.strip(), dictionary))
    match = _NUMERIC.match(lowered)
    if match is not None:
        return (
            "cmp",
            _field(match.group("field"), dictionary),
            match.group("op"),
            int(match.group("value")),
        )
    match = _NUMERIC_WORDS.match(lowered)
    if match is not None:
        op = _WORD_OPS[" ".join(match.group("phrase").split())]
        return (
            "cmp",
            _field(match.group("field"), dictionary),
            op,
            int(match.group("value")),
        )
    if lowered == "vip":
        return ("eq", "vip", True)
    raise OracleUnsupported(f"unsupported condition {body!r}")


def _field(text: str, dictionary: Mapping[str, object]) -> str:
    aliases = dictionary.get("aliases", {})
    assert isinstance(aliases, dict)
    return str(aliases.get(text, text))


def _json_condition(raw: object, dictionary: Mapping[str, object]) -> tuple[object, ...]:
    assert isinstance(raw, dict)
    kind = raw.get("kind")
    if kind == "missing":
        return ("missing", str(raw["field"]))
    if kind == "eq":
        return ("eq", str(raw["field"]), raw["value"])
    if kind == "cmp":
        return ("cmp", str(raw["field"]), str(raw["op"]), raw["value"])
    if kind == "and":
        items = raw.get("items", [])
        result: tuple[object, ...] = ("true",)
        for item in items:
            result = ("and", result, _json_condition(item, dictionary))
        return result
    if kind == "not":
        return ("not", _json_condition(raw["item"], dictionary))
    raise OracleUnsupported(f"unsupported JSON condition {kind!r}")


def parse_requirements(text: str, dictionary: Mapping[str, object]) -> ParsedRequirements:
    destinations = {
        str(item["id"]): str(item["id"])
        for item in dictionary.get("destinations", [])  # type: ignore[union-attr]
    }
    for item in dictionary.get("destinations", []):  # type: ignore[union-attr]
        destinations[str(item["id"]).casefold()] = str(item["id"])
        for alias in item.get("aliases", []):
            destinations[str(alias).casefold()] = str(item["id"])
    normal: list[Rule] = []
    exceptions: list[tuple[str, Rule]] = []
    unsupported: list[str] = []
    evidence: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        evidence[str(number)] = line
        if line.casefold().startswith("exception to "):
            head, sep, rest = line.partition(":")
            if not sep:
                unsupported.append(line)
                continue
            target = head[len("exception to ") :].strip()
            body = rest.strip()
            if not body.casefold().startswith("if "):
                unsupported.append(line)
                continue
            condition_text, sep, dest_text = body[3:].partition(", route to ")
            if not sep:
                unsupported.append(line)
                continue
            try:
                condition = _condition(condition_text, dictionary)
                destination = _destination(dest_text, destinations)
            except OracleUnsupported:
                unsupported.append(line)
                continue
            ref = f"{target}.exception"
            exceptions.append(
                (
                    target,
                    Rule(
                        ref=ref, condition=condition, destination=destination, exception_of=target
                    ),
                )
            )
            continue
        split = _split_rule(_strip_period(line))
        if split is None:
            unsupported.append(line)
            continue
        ref, body = split
        condition_text, sep, dest_text = body.partition("->")
        if not sep:
            unsupported.append(line)
            continue
        try:
            condition = _condition(condition_text, dictionary)
            destination = _destination(dest_text, destinations)
        except OracleUnsupported:
            unsupported.append(line)
            continue
        normal.append(Rule(ref=ref, condition=condition, destination=destination))

    ordered: list[Rule] = []
    for rule in normal:
        for target, exception in exceptions:
            if target == rule.ref:
                ordered.append(
                    Rule(
                        ref=exception.ref,
                        condition=("and", rule.condition, exception.condition),
                        destination=exception.destination,
                        exception_of=rule.ref,
                    )
                )
        ordered.append(rule)
    return ParsedRequirements(
        rules=tuple(ordered), unsupported=tuple(unsupported), evidence=evidence
    )


# --------------------------------------------------------------------------- #
# Approvals
# --------------------------------------------------------------------------- #

_PAYLOAD_FIELDS = ("field", "value", "unit", "op", "destination", "text", "after")
_CHANGE_FIELDS = (
    "change_id",
    "change_version",
    "base_artifact",
    "base_version",
    "target_rule",
    "operation",
    "from",
    "to",
    "sequence",
    "rationale",
)


def _canonical_payload(raw: object) -> dict[str, object]:
    payload = raw if isinstance(raw, dict) else {}
    result: dict[str, object] = {}
    for name in _PAYLOAD_FIELDS:
        value = payload.get(name)
        if name == "value" and value is not None:
            value = str(value)
        result[name] = value
    return result


def change_hash(record: Mapping[str, object]) -> str:
    canonical: dict[str, object] = {}
    for name in _CHANGE_FIELDS:
        if name in {"from", "to"}:
            canonical[name] = _canonical_payload(record.get(name))
        elif name == "rationale":
            canonical[name] = record.get("rationale", "")
        else:
            canonical[name] = record.get(name)
    text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ApprovalOutcome:
    applied: tuple[Rule, ...]
    states: dict[str, str]
    blockers: tuple[str, ...]
    notes: tuple[str, ...]


def _authorized_roles() -> frozenset[str]:
    config_path = ROOT / "configs" / "default.toml"
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        roles = data.get("approvals", {}).get("authorized_roles", ["policy_owner"])
    except (OSError, tomllib.TOMLDecodeError):
        roles = ["policy_owner"]
    return frozenset(str(role).casefold() for role in roles)


def resolve_approvals(
    bundle: Bundle,
    base_rules: tuple[Rule, ...],
    requirements_ref: str,
) -> ApprovalOutcome:
    entry = next(
        (
            item
            for item in bundle.manifest.get("artifacts", [])
            if item["kind"] == "change_requests"
        ),
        None,
    )
    if entry is None:
        return ApprovalOutcome(applied=base_rules, states={}, blockers=(), notes=())
    raw = _json(bundle, str(entry["artifact_id"]))
    records = {str(item["change_id"]): item for item in raw.get("changes", [])}
    authorized = _authorized_roles()
    valid: dict[str, dict[str, object]] = {}
    blockers: list[str] = []
    notes: list[str] = []
    for approval in bundle.manifest.get("approvals", []):
        approval_id = str(approval["approval_id"])
        if str(approval.get("approver_role", "")).casefold() not in authorized:
            notes.append(f"approval {approval_id} ignored: role not authorized")
            continue
        change = records.get(str(approval["change_id"]))
        if change is None:
            blockers.append("INVALID_APPROVAL")
            notes.append(f"approval {approval_id} references an unknown change")
            continue
        if str(approval["change_sha256"]) != change_hash(change):
            blockers.append("INVALID_APPROVAL")
            notes.append(f"approval {approval_id} is bound to a different content hash")
            continue
        valid[approval_id] = approval
    # Supersession (acyclic edges only); our dataset has none, but the rule is part of
    # approval authority.
    superseded: set[str] = set()
    for approval_id, record in valid.items():
        for target in record.get("supersedes", []):
            if target in valid and target != approval_id:
                superseded.add(str(target))
    active: dict[str, list[dict[str, object]]] = {}
    for approval_id, record in valid.items():
        if approval_id in superseded:
            notes.append(f"approval {approval_id} is superseded")
            continue
        active.setdefault(str(record["change_id"]), []).append(record)

    rules = list(base_rules)
    states: dict[str, str] = {}
    for change_id in sorted(records):
        change = records[change_id]
        approvals = active.get(change_id, [])
        if f"{change['base_artifact']}@{change['base_version']}" != requirements_ref:
            blockers.append("STALE_CHANGE")
            states[change_id] = "stale"
            continue
        if not approvals:
            states[change_id] = "unapproved"
            notes.append(f"change {change_id} has no active hash-bound approval")
            continue
        votes = {str(item["decision"]) for item in approvals}
        if len(votes) > 1:
            blockers.append("UNDETERMINED_CONFLICT")
            states[change_id] = "conflict"
            continue
        if "reject" in votes:
            states[change_id] = "rejected"
            notes.append(f"change {change_id} is rejected")
            continue
        target = change.get("target_rule")
        if change.get("operation") != "replace_threshold":
            blockers.append("UNSUPPORTED_CHANGE")
            states[change_id] = "unsupported"
            continue
        if target is None or not any(rule.ref == target for rule in rules):
            blockers.append("UNSUPPORTED_CHANGE")
            states[change_id] = "unsupported"
            continue
        payload = _canonical_payload(change.get("to"))
        value = payload.get("value")
        unit = payload.get("unit")
        updated: list[Rule] = []
        for rule in rules:
            if rule.ref != target:
                updated.append(rule)
                continue
            kind = rule.condition[0]
            if kind != "cmp":
                blockers.append("UNSUPPORTED_CHANGE")
                states[change_id] = "unsupported"
                break
            updated.append(
                Rule(
                    ref=rule.ref,
                    condition=("cmp", rule.condition[1], rule.condition[2], int(str(value))),
                    destination=rule.destination,
                    exception_of=rule.exception_of,
                )
            )
            effective = unit
        else:
            rules = updated
            states[change_id] = "approved"
            notes.append(f"change {change_id} applied: {target} -> {value} {effective}")
    return ApprovalOutcome(
        applied=tuple(rules),
        states=states,
        blockers=tuple(dict.fromkeys(blockers)),
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------- #
# Artifact routing
# --------------------------------------------------------------------------- #


def route_policy(rules: Iterable[Rule], ticket: Mapping[str, object]) -> str | None:
    for rule in rules:
        if evaluate(rule.condition, ticket) == TRUE:
            return rule.destination
    return None


def parse_spec(
    spec: Mapping[str, object], dictionary: Mapping[str, object]
) -> tuple[list[Element], str]:
    elements: list[Element] = []
    rules = sorted(spec.get("rules", []), key=lambda item: item.get("priority", 0))  # type: ignore[union-attr]
    for order, rule in enumerate(rules):
        elements.append(
            Element(
                element_id=str(rule["id"]),
                condition=_json_condition(rule["when"], dictionary),
                destination=str(rule["destination"]),
                order=order,
            )
        )
    default = spec.get("default", {})
    destination = str(default.get("destination", "")) if isinstance(default, dict) else ""
    return elements, destination


def route_elements(
    elements: Iterable[Element], default: str, ticket: Mapping[str, object]
) -> str | None:
    for element in elements:
        if evaluate(element.condition, ticket) == TRUE:
            return element.destination
    return default or None


def parse_workflow(
    workflow: Mapping[str, object], dictionary: Mapping[str, object]
) -> tuple[list[Element], str]:
    nodes = {str(node["id"]): node for node in workflow.get("nodes", [])}  # type: ignore[union-attr]
    start = str(workflow.get("start", ""))
    node = nodes.get(start)
    if node is None or node.get("kind") != "decision":
        raise OracleUnsupported("workflow start is not a decision node")
    elements = []
    for order, branch in enumerate(node.get("branches", [])):
        condition = branch.get("condition")
        elements.append(
            Element(
                element_id=str(branch["branch_id"]),
                condition=("true",)
                if condition is None
                else _json_condition(condition, dictionary),
                destination=_terminal(nodes, str(branch["target"])),
                order=order,
            )
        )
    return elements, ""


def _terminal(nodes: Mapping[str, Mapping[str, object]], node_id: str) -> str:
    seen: set[str] = set()
    current = node_id
    for _ in range(64):
        if current in seen:
            raise OracleUnsupported("workflow cycle")
        seen.add(current)
        node = nodes.get(current)
        if node is None:
            raise OracleUnsupported(f"workflow target {current!r} is not declared")
        if node.get("kind") == "terminal":
            return str(node["destination"])
        if node.get("kind") != "decision":
            raise OracleUnsupported(f"unsupported workflow node kind {node.get('kind')!r}")
        branch = next(
            (item for item in node.get("branches", []) if item.get("condition") is None),
            None,
        )
        if branch is None:
            raise OracleUnsupported("non-terminal decision node has no default branch")
        current = str(branch["target"])
    raise OracleUnsupported("workflow did not terminate")


# --------------------------------------------------------------------------- #
# Restricted code evaluation
# --------------------------------------------------------------------------- #


class CodeUnsupported(OracleUnsupported):
    pass


@dataclass
class CodeProgram:
    conditions: list[tuple[tuple[object, ...], str]]
    fallback: str | None
    destination_map: dict[str, str]
    constants: dict[str, str]


def parse_code(source: str, entrypoint: str, destination_map: Mapping[str, str]) -> CodeProgram:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise CodeUnsupported(f"invalid python: {exc}") from exc
    constants: dict[str, str] = {}
    function: ast.FunctionDef | None = None
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            if (
                isinstance(target, ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                constants[target.id] = node.value.value
                continue
            raise CodeUnsupported(f"unsupported assignment at line {node.lineno}")
        if isinstance(node, ast.FunctionDef) and node.name == entrypoint:
            function = node
            continue
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
            continue
        raise CodeUnsupported(f"unsupported module statement {type(node).__name__}")
    if function is None:
        raise CodeUnsupported(f"entrypoint {entrypoint!r} is not defined")
    if len(function.args.args) != 1:
        raise CodeUnsupported("entrypoint must take exactly one argument")
    parameter = function.args.args[0].arg
    conditions: list[tuple[tuple[object, ...], str]] = []
    fallback: str | None = None
    for statement in function.body:
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue
        if isinstance(statement, ast.Return):
            fallback = _literal(statement.value, constants)
            continue
        if isinstance(statement, ast.If):
            if fallback is not None:
                raise CodeUnsupported("statement after the default return")
            if len(statement.body) != 1 or not isinstance(statement.body[0], ast.Return):
                raise CodeUnsupported("if body must be a single return")
            destination = _literal(statement.body[0].value, constants)
            conditions.append((_test(statement.test, parameter), destination))
            continue
        raise CodeUnsupported(f"unsupported statement {type(statement).__name__}")
    if fallback is None:
        raise CodeUnsupported("entrypoint can fall through without a return")
    return CodeProgram(
        conditions=conditions,
        fallback=fallback,
        destination_map=dict(destination_map),
        constants=constants,
    )


def _literal(node: ast.expr | None, constants: Mapping[str, str]) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    raise CodeUnsupported("return value must be a string literal or constant")


def _ticket_key(node: ast.expr, parameter: str) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id == parameter:
        return parameter
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == parameter
    ):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value
    raise CodeUnsupported("unsupported ticket expression")


def _get_call(node: ast.expr, parameter: str) -> str:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == parameter
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.args[0].value
    raise CodeUnsupported("unsupported ticket.get call")


def _test(node: ast.expr, parameter: str) -> tuple[object, ...]:
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        result: tuple[object, ...] = ("true",)
        for value in node.values:
            result = ("and", result, _test(value, parameter))
        return result
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return ("not", _test(node.operand, parameter))
    if isinstance(node, ast.Subscript):
        return ("truthy", _ticket_key(node, parameter))
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        op = node.ops[0]
        left = node.left
        right = node.comparators[0]
        if isinstance(op, (ast.In, ast.NotIn)):
            key = _ticket_key(left, parameter)
            if key == parameter:
                raise CodeUnsupported("membership over the whole ticket")
            return ("present" if isinstance(op, ast.In) else "absent", key)
        if isinstance(op, (ast.Is, ast.IsNot)):
            key = _get_call(left, parameter)
            if not (isinstance(right, ast.Constant) and right.value is None):
                raise CodeUnsupported("only `is None` is supported")
            return ("absent" if isinstance(op, ast.Is) else "present", key)
        if isinstance(op, (ast.Eq, ast.NotEq)):
            if isinstance(left, ast.Subscript) and isinstance(left.value, ast.Name):
                key = _ticket_key(left, parameter)
                access = "direct"
            elif isinstance(left, ast.Call):
                key = _get_call(left, parameter)
                access = "get"
            else:
                raise CodeUnsupported("unsupported equality left side")
            if not isinstance(right, ast.Constant):
                raise CodeUnsupported("comparison value must be a literal")
            condition: tuple[object, ...] = (
                "code_eq",
                key,
                right.value,
                access,
                isinstance(op, ast.Eq),
            )
            return condition
        if isinstance(op, (ast.Gt, ast.GtE, ast.Lt, ast.LtE)):
            if isinstance(left, ast.Subscript) and isinstance(left.value, ast.Name):
                key = _ticket_key(left, parameter)
            elif isinstance(left, ast.Call):
                key = _get_call(left, parameter)
            else:
                raise CodeUnsupported("unsupported comparison left side")
            if not isinstance(right, ast.Constant) or not isinstance(right.value, int):
                raise CodeUnsupported("comparison value must be an integer")
            symbols = {ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<="}
            return ("code_cmp", key, symbols[type(op)], right.value)
    raise CodeUnsupported(f"unsupported test {ast.dump(node)[:80]}")


def _code_truth(condition: tuple[object, ...], ticket: Mapping[str, object]) -> str:
    kind = condition[0]
    if kind == "true":
        return TRUE
    if kind == "and":
        left = _code_truth(condition[1], ticket)  # type: ignore[arg-type]
        if left == FALSE:
            return FALSE
        return truth_and(left, _code_truth(condition[2], ticket))  # type: ignore[arg-type]
    if kind == "or":
        left = _code_truth(condition[1], ticket)  # type: ignore[arg-type]
        if left == TRUE:
            return TRUE
        return truth_or(left, _code_truth(condition[2], ticket))  # type: ignore[arg-type]
    if kind == "not":
        return truth_not(_code_truth(condition[1], ticket))  # type: ignore[arg-type]
    if kind == "truthy":
        return TRUE if _index(ticket, str(condition[1])) else FALSE
    key = str(condition[1])
    if kind == "present":
        return TRUE if key in ticket else FALSE
    if kind == "absent":
        return TRUE if key not in ticket or ticket[key] is None else FALSE
    if kind == "code_eq":
        value = ticket.get(key, None) if condition[3] == "get" else _index(ticket, key)
        if value is None:
            return FALSE
        equal = value == condition[2]
        return TRUE if equal is condition[4] else FALSE
    if kind == "code_cmp":
        value = _index(ticket, key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise OracleUnsupported("non-integer code comparison")
        return TRUE if _compare(value, str(condition[2]), condition[3]) else FALSE
    raise CodeUnsupported(f"unknown code condition {kind!r}")


def _index(ticket: Mapping[str, object], key: str) -> object:
    if key not in ticket:
        raise KeyError(key)
    return ticket[key]


def route_code(program: CodeProgram, ticket: Mapping[str, object]) -> tuple[str, str | None]:
    """Return (outcome, destination-or-reason)."""
    try:
        for condition, destination in program.conditions:
            if _code_truth(condition, ticket) == TRUE:
                return _map_destination(program.destination_map, destination)
        return _map_destination(program.destination_map, program.fallback or "")
    except KeyError as exc:
        return "error", str(exc)


def _map_destination(mapping: Mapping[str, str], raw: str) -> tuple[str, str | None]:
    if raw not in mapping:
        return "unsupported", raw
    return "destination", mapping[raw]


# --------------------------------------------------------------------------- #
# Structural coverage
# --------------------------------------------------------------------------- #


def _shape(condition: tuple[object, ...]) -> tuple[object, ...]:
    kind = condition[0]
    if kind == "and":
        return ("and", _shape(condition[1]), _shape(condition[2]))  # type: ignore[arg-type]
    if kind == "not":
        return ("not", _shape(condition[1]))  # type: ignore[arg-type]
    if kind in {"missing", "eq", "cmp"}:
        field_name = condition[1]
        return (kind, field_name)
    return (kind,)


def _same_condition(left: tuple[object, ...], right: tuple[object, ...]) -> bool:
    return left == right


def _semantic_fields(condition: tuple[object, ...]) -> set[str]:
    kind = condition[0]
    if kind in {
        "true",
    }:
        return set()
    if kind in {"and", "or"}:
        return _semantic_fields(condition[1]) | _semantic_fields(condition[2])  # type: ignore[arg-type]
    if kind == "not":
        return _semantic_fields(condition[1])  # type: ignore[arg-type]
    return {str(condition[1])}


def equivalent(left: tuple[object, ...], right: tuple[object, ...]) -> bool:
    """Truth-table equivalence over the declared support-ticket domain."""
    import itertools

    fields = sorted(_semantic_fields(left) | _semantic_fields(right))
    for field_name in fields:
        if field_name not in _COND_VALUES:
            return left == right
    for combo in itertools.product(*(_COND_VALUES[name] for name in fields)):
        ticket = {
            name: value for name, value in zip(fields, combo, strict=True) if value is not MISSING
        }
        if evaluate(left, ticket) != evaluate(right, ticket):
            return False
    return True


_COND_VALUES: dict[str, tuple[object, ...]] = {
    "category": (MISSING, "billing", "technical", "general"),
    "priority": (MISSING, "urgent", "normal"),
    "wait_time": (MISSING, -1, 0, 24, 25, 28, 29, 30, 31, 60),
    "vip": (MISSING, True, False),
}


def structural_coverage(
    rule: Rule, elements: tuple[Element, ...], artifact: str
) -> tuple[str | None, str | None]:
    """Return (defect class, matched element id) for one effective rule."""
    for element in elements:
        if equivalent(element.condition, rule.condition):
            if element.destination == rule.destination:
                return None, element.element_id
            return "WRONG_DESTINATION", element.element_id
    candidates = [
        element for element in elements if _shape(element.condition) == _shape(rule.condition)
    ]
    for element in candidates:
        if element.destination != rule.destination:
            continue
        if rule.condition[0] == "cmp" and element.condition[0] == "cmp":
            return "THRESHOLD_CHANGED", element.element_id
        if rule.exception_of is not None:
            return "MISSING_EXCEPTION", element.element_id
        return (
            "MISSING_REQUIREMENT" if artifact == "spec" else "MISSING_BRANCH"
        ), element.element_id
    if rule.exception_of is not None:
        return "MISSING_EXCEPTION", None
    return ("MISSING_REQUIREMENT" if artifact == "spec" else "MISSING_BRANCH"), None


# --------------------------------------------------------------------------- #
# Tickets
# --------------------------------------------------------------------------- #

CATEGORIES = (MISSING, "billing", "technical", "general")
PRIORITIES = (MISSING, "urgent", "normal")
WAITS = (MISSING, -1, 0, 24, 25, 28, 29, 30, 31, 60)
VIPS = (MISSING, True, False)


def witness_tickets() -> list[dict[str, object]]:
    tickets: list[dict[str, object]] = []
    for category in CATEGORIES:
        for priority in PRIORITIES:
            for wait in WAITS:
                for vip in VIPS:
                    ticket: dict[str, object] = {}
                    if category is not MISSING:
                        ticket["category"] = category
                    if priority is not MISSING:
                        ticket["priority"] = priority
                    if wait is not MISSING:
                        ticket["wait_time"] = wait
                    if vip is not MISSING:
                        ticket["vip"] = vip
                    tickets.append(ticket)
    return tickets


def render_ticket(ticket: Mapping[str, object]) -> str:
    parts = []
    for name in ("category", "priority", "wait_time", "vip"):
        value = ticket.get(name, MISSING)
        parts.append(f"{name}={value}")
    return ",".join(parts)


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def _review_artifact(
    name: str,
    expected_route: dict[str, str | None],
    route: object,
    rules: tuple[Rule, ...],
    elements: tuple[Element, ...],
    tickets: list[dict[str, object]],
) -> ArtifactReview:
    differences: list[ArtifactDiff] = []
    runtime_errors: list[ArtifactDiff] = []
    unsupported: list[ArtifactDiff] = []
    coverage: list[str] = []
    matched_orders: list[int] = []
    for rule in rules:
        defect, element_id = structural_coverage(rule, elements, name)
        if defect is not None:
            coverage.append(defect)
        elif element_id is not None:
            matched_orders.append(
                next(element.order for element in elements if element.element_id == element_id)
            )
    if len(matched_orders) == len(rules) and matched_orders != sorted(matched_orders):
        coverage.append("PRECEDENCE")
    for ticket in tickets:
        key = render_ticket(ticket)
        expected = expected_route[key]
        try:
            outcome, actual = route(ticket)  # type: ignore[operator]
        except OracleUnsupported as exc:
            return ArtifactReview(
                name=name,
                supported=False,
                coverage=tuple(dict.fromkeys(coverage)),
                detail=str(exc),
            )
        if outcome == "error":
            runtime_errors.append(ArtifactDiff(ticket=ticket, expected=expected, actual=actual))
        elif outcome == "unsupported":
            unsupported.append(ArtifactDiff(ticket=ticket, expected=expected, actual=actual))
        elif actual != expected:
            differences.append(ArtifactDiff(ticket=ticket, expected=expected, actual=actual))
    return ArtifactReview(
        name=name,
        supported=True,
        differences=tuple(differences),
        runtime_errors=tuple(runtime_errors),
        unsupported_destinations=tuple(unsupported),
        coverage=tuple(dict.fromkeys(coverage)),
    )


def evaluate_bundle(path: Path) -> OracleResult:
    bundle = load_bundle(path)
    notes: list[str] = list(bundle.provenance_errors)
    incomplete: list[str] = []
    if bundle.provenance_errors:
        incomplete.append("PROVENANCE_FAILURE")

    dictionary = _json(bundle, "DICT")
    parsed = parse_requirements(_text(bundle, "REQ"), dictionary)
    if parsed.unsupported:
        incomplete.append("UNSUPPORTED_LANGUAGE")
        notes.append(f"unsupported requirement lines: {parsed.unsupported}")

    entry = _artifact_entry(bundle, "REQ")
    requirements_ref = f"{entry['artifact_id']}@{entry['version']}" if entry else ""
    approvals = resolve_approvals(bundle, parsed.rules, requirements_ref)
    incomplete.extend(approvals.blockers)
    notes.extend(approvals.notes)
    rules = approvals.applied

    tickets = witness_tickets()
    expected_route = {render_ticket(ticket): route_policy(rules, ticket) for ticket in tickets}
    catch_all = next((rule for rule in rules if rule.condition[0] == "true"), None)
    default_destination = catch_all.destination if catch_all is not None else ""

    artifacts: dict[str, ArtifactReview] = {}
    blockers: list[str] = []

    if "SPEC" in bundle.artifacts:
        elements, default = parse_spec(_json(bundle, "SPEC"), dictionary)
        spec_rules = tuple(rule for rule in rules if rule.condition[0] != "true")
        review = _review_artifact(
            "spec",
            expected_route,
            lambda ticket, elements=tuple(elements), default=default: (
                "destination",
                route_elements(elements, default, ticket),
            ),
            spec_rules,
            elements,
            tickets,
        )
        artifacts["spec"] = review
    elif any("spec.json" in error for error in bundle.provenance_errors):
        review = ArtifactReview(
            name="spec", supported=False, detail="spec artifact failed provenance"
        )
        artifacts["spec"] = review
    else:
        review = ArtifactReview(name="spec", supported=False, detail="no spec linked")
        artifacts["spec"] = review

    if "WF" in bundle.artifacts:
        elements, default = parse_workflow(_json(bundle, "WF"), dictionary)
        workflow_rules = tuple(rule for rule in rules if rule.condition[0] != "true")
        review = _review_artifact(
            "workflow",
            expected_route,
            lambda ticket, elements=tuple(elements), default=default: (
                "destination",
                route_elements(elements, default, ticket),
            ),
            workflow_rules,
            elements,
            tickets,
        )
        artifacts["workflow"] = review
    else:
        review = ArtifactReview(
            name="workflow", supported=False, detail="workflow failed provenance"
        )
        artifacts["workflow"] = review

    code_entry = next(
        (entry for entry in bundle.manifest.get("artifacts", []) if entry["kind"] == "python"),
        None,
    )
    if code_entry is not None:
        link = code_entry.get("link", {})
        try:
            program = parse_code(
                _text(bundle, str(code_entry["artifact_id"])),
                str(link.get("entrypoint", "route")),
                link.get("destination_map", {}),
            )
        except CodeUnsupported as exc:
            artifacts["code"] = ArtifactReview(name="code", supported=False, detail=str(exc))
            incomplete.append("UNSUPPORTED_CODE")
            program = None
        else:
            artifacts["code"] = _review_artifact(
                "code",
                expected_route,
                lambda ticket, program=program: route_code(program, ticket),
                (),
                (),
                tickets,
            )
    else:
        artifacts["code"] = ArtifactReview(name="code", supported=False, detail="no code linked")
        program = None

    for name, review in artifacts.items():
        if name == "code":
            assert program is not None or not review.supported
            if review.runtime_errors:
                blockers.append("RUNTIME_ERROR")
            elif review.differences:
                blockers.extend(_classify_code(program, rules, review))
            continue
        blockers.extend(review.coverage)

    defect_classes = tuple(dict.fromkeys([*blockers, *incomplete]))
    if blockers:
        status = "CHANGES_REQUIRED"
        primary = blockers[0]
    elif incomplete:
        status = "INCOMPLETE"
        primary = incomplete[0]
    else:
        status = "PASS"
        primary = None

    sample_keys = [
        "category=<absent>,priority=urgent,wait_time=<absent>,vip=<absent>",
        "category=<absent>,priority=urgent,wait_time=30,vip=True",
        "category=billing,priority=urgent,wait_time=30,vip=True",
        "category=billing,priority=normal,wait_time=25,vip=False",
        "category=billing,priority=normal,wait_time=29,vip=True",
        "category=billing,priority=normal,wait_time=30,vip=False",
        "category=billing,priority=normal,wait_time=<absent>,vip=False",
        "category=<absent>,priority=normal,wait_time=60,vip=False",
    ]
    tickets_sample = {key: expected_route[key] for key in sample_keys if key in expected_route}

    return OracleResult(
        bundle_id=bundle.bundle_id,
        status=status,
        defect_classes=defect_classes,
        primary_reason=primary,
        effective={
            "rules": [
                {
                    "ref": rule.ref,
                    "condition": list(rule.condition),
                    "destination": rule.destination,
                    "exception_of": rule.exception_of,
                }
                for rule in rules
            ],
            "default": default_destination,
            "approval_states": approvals.states,
            "unsupported_requirements": list(parsed.unsupported),
        },
        tickets=tickets_sample,
        artifacts=artifacts,
        notes=tuple(notes),
    )


def _simplify_code(condition: tuple[object, ...]) -> tuple[object, ...]:
    kind = condition[0]
    if kind == "and":
        left = _simplify_code(condition[1])  # type: ignore[arg-type]
        right = _simplify_code(condition[2])  # type: ignore[arg-type]
        if left == ("true",):
            return right
        if right == ("true",):
            return left
        if left[0] == "present" and right[0] in {"eq", "cmp", "truthy"} and left[1] == right[1]:
            return right
        return ("and", left, right)
    if kind == "code_eq":
        operator = "eq" if condition[4] else "ne"
        return (operator, str(condition[1]), condition[2])
    if kind == "code_cmp":
        return ("cmp", str(condition[1]), str(condition[2]), condition[3])
    if kind == "absent":
        return ("missing", str(condition[1]))
    return condition


def _classify_code(
    program: CodeProgram, rules: tuple[Rule, ...], review: ArtifactReview
) -> list[str]:
    expected = [(rule.condition, rule.destination) for rule in rules if rule.condition[0] != "true"]
    actual = [
        (_simplify_code(condition), program.destination_map.get(destination, ""))
        for condition, destination in program.conditions
    ]
    classes: list[str] = []
    if review.unsupported_destinations:
        classes.extend(("UNSUPPORTED_CODE", "SCRIPT_DISAGREEMENT"))
    remaining = list(expected)
    unmatched: list[tuple[tuple[object, ...], str]] = []
    for condition, destination in actual:
        for index, (expected_condition, expected_destination) in enumerate(remaining):
            if expected_destination == destination and equivalent(condition, expected_condition):
                del remaining[index]
                break
        else:
            unmatched.append((condition, destination))
    if not remaining and not unmatched:
        classes.append("PRECEDENCE")
        return list(dict.fromkeys(classes))
    for expected_condition, expected_destination in expected:
        if expected_condition[0] != "cmp":
            continue
        for actual_condition, actual_destination in actual:
            if (
                actual_condition[0] == "cmp"
                and actual_destination == expected_destination
                and actual_condition[1] == expected_condition[1]
                and actual_condition[2:] != expected_condition[2:]
            ):
                classes.append("THRESHOLD_CHANGED")
    if not classes:
        classes.append("SCRIPT_DISAGREEMENT")
    return list(dict.fromkeys(classes))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python evaluation/oracle.py <bundle-dir>")
        return 2
    result = evaluate_bundle(Path(argv[1]))
    print(json.dumps(result.to_json(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv))
