from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, Protocol, cast

from verityflow.config import Config
from verityflow.ir.model import (
    And,
    Cmp,
    Expr,
    NotNode,
    NotTrue,
    Or,
    canonical_key,
)
from verityflow.ir.normalize import normalize_condition
from verityflow.ir.policy import (
    ChangeDecision,
    ChangeState,
    EffectiveRequirements,
    Policy,
    Rule,
    UndeterminedScope,
)
from verityflow.ir.units import UnitError, normalize
from verityflow.models.changes import ChangeArtifact, ChangeOperation, ChangePayload, ChangeRequest
from verityflow.models.common import TextSpan
from verityflow.models.dictionary import DataDictionary, FieldDecl
from verityflow.models.manifest import (
    ApprovalDecision,
    ApprovalRecord,
    ArtifactEntry,
    ArtifactKind,
    BundleManifest,
)
from verityflow.models.report import (
    Finding,
    FindingCategory,
    Severity,
    make_finding,
)
from verityflow.requirements.parser import parse_condition_text

_ALLOWED_OPS_VIEW = frozenset({"<", "<=", ">", ">=", "==", "!="})
_Op = Literal["<", "<=", ">", ">=", "==", "!="]
_BUNDLE_ARTIFACT = "bundle.json"


class _ReportChange(Protocol):
    def __call__(
        self,
        category: FindingCategory,
        severity: Severity,
        message: str,
        change: ChangeRequest | None = None,
    ) -> None: ...


class _ReportApproval(Protocol):
    def __call__(
        self,
        category: FindingCategory,
        severity: Severity,
        message: str,
        rule_ref: str | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class _Applied:
    rules: tuple[Rule, ...]
    attribute: tuple[str, ...]
    effect_key: str


@dataclass(frozen=True, slots=True)
class _Failure:
    state: ChangeState
    message: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    change: ChangeRequest
    ref: str
    applied: _Applied


def resolve_effective_requirements(
    base_policy: Policy,
    *,
    manifest: BundleManifest,
    changes: ChangeArtifact,
    dictionary: DataDictionary,
    config: Config,
    requirements_ref: str,
    changes_ref: str | None = None,
) -> EffectiveRequirements:
    """Resolve approved changes into a confirmed policy plus diagnostic variants.

    Authority comes only from manifest approval records whose role is authorized and
    whose bound id/version/content hash all match the change under review. Conflicts
    never produce an effective policy: the affected scope is reported as undetermined
    and alternatives are labelled diagnostic-only.
    """
    findings: list[Finding] = []
    change_entry = _change_entry(manifest, changes_ref)
    change_artifact_id = change_entry.artifact_id if change_entry is not None else None
    change_artifact_version = change_entry.version if change_entry is not None else None

    def report_change(
        category: FindingCategory,
        severity: Severity,
        message: str,
        change: ChangeRequest | None = None,
    ) -> None:
        findings.append(
            make_finding(
                category,
                severity,
                message,
                rule_ref=change.target_rule if change is not None else None,
                artifact_id=change_artifact_id,
                artifact_version=change_artifact_version,
            )
        )

    def report_approval(
        category: FindingCategory,
        severity: Severity,
        message: str,
        rule_ref: str | None = None,
    ) -> None:
        findings.append(
            make_finding(
                category,
                severity,
                message,
                rule_ref=rule_ref,
                artifact_id=_BUNDLE_ARTIFACT,
                artifact_version=manifest.bundle_version,
            )
        )

    active = _bind_approvals(manifest, changes, config, report_approval)

    decisions: dict[tuple[str, str], _Decision] = {}
    candidates: list[_Candidate] = []
    for change in sorted(changes.changes, key=lambda item: (item.change_id, item.change_version)):
        key = (change.change_id, change.change_version)
        ref = f"{change.change_id}@{change.change_version}"
        approvals = active.get(key, ())
        approval_ids = tuple(sorted(record.approval_id for record in approvals))

        if f"{change.base_artifact}@{change.base_version}" != requirements_ref:
            decisions[key] = _Decision(
                ChangeState.STALE,
                f"change targets {change.base_artifact}@{change.base_version}, "
                f"but the effective requirements are {requirements_ref}",
                approval_ids,
                change,
            )
            continue
        if not approvals:
            decisions[key] = _Decision(
                ChangeState.UNAPPROVED,
                "no active, hash-bound approval record for this change version",
                (),
                change,
            )
            continue
        votes = {record.decision for record in approvals}
        if len(votes) > 1:
            decisions[key] = _Decision(
                ChangeState.CONFLICT,
                "active approval records disagree (approve and reject)",
                approval_ids,
                change,
            )
            continue
        if ApprovalDecision.REJECT in votes:
            decisions[key] = _Decision(
                ChangeState.REJECTED,
                "active approval record rejects this change; it is not applied",
                approval_ids,
                change,
            )
            continue

        planned = _apply_change(change, base_policy.rules, dictionary)
        if isinstance(planned, _Failure):
            decisions[key] = _Decision(planned.state, planned.message, approval_ids, change)
            continue
        candidate = _Candidate(change=change, ref=ref, applied=planned)
        decisions[key] = _Decision(
            ChangeState.APPROVED,
            "approved; pending application",
            approval_ids,
            change,
        )
        candidates.append(candidate)

    conflicted: dict[str, _Candidate] = {}
    scopes: list[UndeterminedScope] = []
    for _key, decision in decisions.items():
        if decision.state is not ChangeState.CONFLICT:
            continue
        change = decision.change
        ref = f"{change.change_id}@{change.change_version}"
        match = next((item for item in candidates if item.ref == ref), None)
        if match is None:
            planned = _apply_change(change, base_policy.rules, dictionary)
            if not isinstance(planned, _Failure):
                match = _Candidate(change=change, ref=ref, applied=planned)
                candidates.append(match)
        if match is not None:
            conflicted[match.ref] = match
        scopes.append(
            UndeterminedScope(
                rule_ref=change.target_rule or ref,
                attribute="approval",
                reason=decision.reason,
                change_ids=(ref,),
            )
        )

    for component in _conflict_components(candidates):
        for item in component:
            conflicted[item.ref] = item
        refs = tuple(sorted(item.ref for item in component))
        targets = sorted({item.change.target_rule or item.ref for item in component})
        labels = sorted({_attribute_label(item.applied.attribute) for item in component})
        scopes.append(
            UndeterminedScope(
                rule_ref=",".join(targets),
                attribute=",".join(labels),
                reason="approved changes conflict on the same scope and cannot be ordered",
                change_ids=refs,
            )
        )

    ordered = sorted(
        (item for item in candidates if item.ref not in conflicted),
        key=lambda item: (
            item.change.sequence is None,
            item.change.sequence if item.change.sequence is not None else 0,
            item.change.change_id,
            item.change.change_version,
        ),
    )
    applied_rules = base_policy.rules
    applied_refs: list[str] = []
    for item in ordered:
        result = _apply_change(item.change, applied_rules, dictionary)
        key = (item.change.change_id, item.change.change_version)
        if isinstance(result, _Failure):
            decisions[key] = _Decision(
                result.state,
                result.message,
                decisions[key].approval_ids,
                item.change,
            )
            continue
        applied_rules = result.rules
        applied_refs.append(item.ref)
        decisions[key] = _Decision(
            ChangeState.APPROVED,
            "approved; applied",
            decisions[key].approval_ids,
            item.change,
        )

    scope_tuple = _dedupe_scopes(scopes)
    confirmed = Policy(
        candidate_id="confirmed",
        rules=_with_precedence(applied_rules),
        applied_changes=tuple(applied_refs),
        undetermined=scope_tuple,
    )

    policies: list[Policy] = [confirmed]
    variants: list[_Candidate] = sorted(
        conflicted.values(), key=lambda item: (item.change.change_id, item.change.change_version)
    )
    capacity = max(0, config.limits.max_candidate_policies - 1)
    skipped: list[str] = []
    for item in variants:
        if len(policies) - 1 >= capacity:
            skipped.append(item.ref)
            continue
        result = _apply_change(item.change, confirmed.rules, dictionary)
        if isinstance(result, _Failure):
            continue
        policies.append(
            Policy(
                candidate_id=f"variant:{item.ref}",
                diagnostic_only=True,
                rules=result.rules,
                applied_changes=(*applied_refs, item.ref),
                undetermined=scope_tuple,
            )
        )
    if skipped:
        report_change(
            FindingCategory.UNDETERMINED_CONFLICT,
            Severity.COVERAGE_GAP,
            "max_candidate_policies was reached; diagnostic variants for "
            f"{', '.join(skipped)} were not materialized and their scope remains undetermined",
        )

    for _key, decision in sorted(decisions.items()):
        entry = _STATE_FINDINGS.get(decision.state)
        if entry is None:
            continue
        category, severity = entry
        ref = f"{decision.change.change_id}@{decision.change.change_version}"
        report_change(
            category,
            severity,
            f"change {ref}: {decision.reason}",
            decision.change,
        )

    return EffectiveRequirements(
        bundle_id=manifest.bundle_id,
        requirements_ref=requirements_ref,
        candidates=tuple(policies),
        confirmed_candidate_id=confirmed.candidate_id,
        undetermined=scope_tuple,
        applied=tuple(applied_refs),
        decisions=tuple(
            ChangeDecision(
                change_id=decision.change.change_id,
                change_version=decision.change.change_version,
                state=decision.state,
                reason=decision.reason,
                approval_ids=decision.approval_ids,
                content_hash=decision.change.content_hash(),
            )
            for _, decision in sorted(decisions.items())
        ),
        findings=_dedupe_findings(findings),
    )


@dataclass(frozen=True, slots=True)
class _Decision:
    state: ChangeState
    reason: str
    approval_ids: tuple[str, ...]
    change: ChangeRequest


_STATE_FINDINGS: dict[ChangeState, tuple[FindingCategory, Severity]] = {
    ChangeState.CONFLICT: (FindingCategory.UNDETERMINED_CONFLICT, Severity.COVERAGE_GAP),
    ChangeState.STALE: (FindingCategory.STALE_CHANGE, Severity.COVERAGE_GAP),
    ChangeState.UNSUPPORTED: (FindingCategory.UNSUPPORTED_CHANGE_OPERATION, Severity.COVERAGE_GAP),
    ChangeState.INVALID_APPROVAL: (FindingCategory.INVALID_APPROVAL, Severity.COVERAGE_GAP),
    ChangeState.UNAPPROVED: (FindingCategory.UNAPPROVED_CHANGE, Severity.INFO),
    ChangeState.REJECTED: (FindingCategory.REJECTED_CHANGE, Severity.INFO),
}


def _change_entry(manifest: BundleManifest, changes_ref: str | None) -> ArtifactEntry | None:
    if changes_ref is not None:
        return manifest.entry(changes_ref)
    entries = manifest.entries_of_kind(ArtifactKind.CHANGE_REQUESTS)
    if len(entries) == 1:
        return entries[0]
    return None


def _bind_approvals(
    manifest: BundleManifest,
    changes: ChangeArtifact,
    config: Config,
    report_approval: _ReportApproval,
) -> dict[tuple[str, str], tuple[ApprovalRecord, ...]]:
    authorized_roles = {role.casefold() for role in config.approvals.authorized_roles}
    valid: dict[str, ApprovalRecord] = {}
    valid_keys: dict[str, tuple[str, str]] = {}
    for record in sorted(manifest.approvals, key=lambda item: item.approval_id):
        if record.approver_role.casefold() not in authorized_roles:
            report_approval(
                FindingCategory.APPROVAL_IGNORED,
                Severity.INFO,
                f"approval {record.approval_id!r} comes from unauthorized role "
                f"{record.approver_role!r} and grants no authority",
            )
            continue
        key = (record.change_id, record.change_version)
        change = changes.by_id(record.change_id, record.change_version)
        if change is None:
            report_approval(
                FindingCategory.INVALID_APPROVAL,
                Severity.COVERAGE_GAP,
                f"approval {record.approval_id!r} references change "
                f"{record.change_id}@{record.change_version}, which is not in the "
                "reviewed change artifact",
            )
            continue
        if change.content_hash() != record.change_sha256:
            report_approval(
                FindingCategory.INVALID_APPROVAL,
                Severity.COVERAGE_GAP,
                f"approval {record.approval_id!r} is bound to a different content hash; "
                f"the change {change.change_id}@{change.change_version} is treated as "
                "unapproved",
                change.target_rule,
            )
            continue
        valid[record.approval_id] = record
        valid_keys[record.approval_id] = key

    superseded: dict[str, set[str]] = {}
    edges: dict[str, set[str]] = {}
    candidate_edges: list[tuple[str, str]] = []
    for approval_id in sorted(valid):
        record = valid[approval_id]
        for target in record.supersedes:
            if target == approval_id:
                report_approval(
                    FindingCategory.APPROVAL_IGNORED,
                    Severity.WARNING,
                    f"approval {approval_id!r} supersedes itself; the edge is ignored",
                )
                continue
            if target not in valid:
                report_approval(
                    FindingCategory.APPROVAL_IGNORED,
                    Severity.WARNING,
                    f"approval {approval_id!r} supersedes unknown or invalid approval "
                    f"{target!r}; the edge is ignored",
                )
                continue
            edges.setdefault(approval_id, set()).add(target)
            candidate_edges.append((approval_id, target))

    for approval_id, target in candidate_edges:
        if _reachable(target, approval_id, edges):
            report_approval(
                FindingCategory.APPROVAL_IGNORED,
                Severity.WARNING,
                f"supersession cycle between {approval_id!r} and {target!r} is ignored",
            )
            continue
        superseded.setdefault(target, set()).add(approval_id)

    active_by_key: dict[tuple[str, str], list[ApprovalRecord]] = {}
    for approval_id, record in valid.items():
        if approval_id in superseded:
            report_approval(
                FindingCategory.APPROVAL_IGNORED,
                Severity.INFO,
                f"approval {approval_id!r} is superseded by "
                f"{', '.join(sorted(superseded[approval_id]))} and grants no authority",
            )
            continue
        active_by_key.setdefault(valid_keys[approval_id], []).append(record)
    return {
        key: tuple(sorted(records, key=lambda item: item.approval_id))
        for key, records in active_by_key.items()
    }


def _reachable(start: str, goal: str, edges: dict[str, set[str]]) -> bool:
    stack = [start]
    seen: set[str] = set()
    while stack:
        node = stack.pop()
        if node == goal:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))
    return False


def _conflict_components(candidates: list[_Candidate]) -> list[list[_Candidate]]:
    adjacency: dict[str, set[str]] = {item.ref: set() for item in candidates}
    by_ref = {item.ref: item for item in candidates}
    for index, first in enumerate(candidates):
        for second in candidates[index + 1 :]:
            if _pairs_conflict(first, second):
                adjacency[first.ref].add(second.ref)
                adjacency[second.ref].add(first.ref)
    components: list[list[_Candidate]] = []
    seen: set[str] = set()
    for ref in sorted(adjacency):
        if ref in seen or not adjacency[ref]:
            continue
        stack = [ref]
        component: list[str] = []
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            component.append(node)
            stack.extend(sorted(adjacency[node]))
        components.append([by_ref[item] for item in sorted(component)])
    return components


def _pairs_conflict(first: _Candidate, second: _Candidate) -> bool:
    if _strictly_ordered(first, second):
        return False
    if first.applied.attribute == second.applied.attribute:
        return first.applied.effect_key != second.applied.effect_key
    first_rule = first.applied.attribute[0]
    second_rule = second.applied.attribute[0]
    if first_rule != second_rule:
        return False
    remove = {"rule"}
    return (len(first.applied.attribute) > 1 and first.applied.attribute[1] in remove) or (
        len(second.applied.attribute) > 1 and second.applied.attribute[1] in remove
    )


def _strictly_ordered(first: _Candidate, second: _Candidate) -> bool:
    one = first.change.sequence
    two = second.change.sequence
    return one is not None and two is not None and one != two


def _attribute_label(attribute: tuple[str, ...]) -> str:
    if len(attribute) > 1:
        return attribute[1]
    return attribute[0]


def _apply_change(
    change: ChangeRequest,
    rules: tuple[Rule, ...],
    dictionary: DataDictionary,
) -> _Applied | _Failure:
    operation = change.operation
    if operation is ChangeOperation.ADD_RULE:
        return _apply_add(change, rules, dictionary)
    target_ref = change.target_rule
    if target_ref is None:
        return _Failure(ChangeState.STALE, "change does not name a target rule")
    index = next((i for i, rule in enumerate(rules) if rule.rule_ref == target_ref), None)
    if index is None:
        return _Failure(
            ChangeState.STALE,
            f"target rule {target_ref!r} does not exist in the base requirements",
        )
    rule = rules[index]
    if rule.exception_of is not None and operation is not ChangeOperation.REPLACE_DESTINATION:
        return _Failure(
            ChangeState.UNSUPPORTED,
            f"rule {target_ref!r} is an exception; only destination changes target "
            "an exception directly",
        )
    if operation is ChangeOperation.REMOVE_RULE:
        remaining = [
            item
            for item in rules
            if item.rule_ref != target_ref and item.exception_of != target_ref
        ]
        return _Applied(
            rules=_with_precedence(tuple(remaining)),
            attribute=(target_ref, "rule"),
            effect_key="removed",
        )
    if operation is ChangeOperation.REPLACE_DESTINATION:
        return _apply_destination(change, rules, index, rule, dictionary)
    if operation is ChangeOperation.REPLACE_OPERATOR:
        return _apply_operator(change, rules, index, rule)
    if operation is ChangeOperation.REPLACE_CONDITION:
        return _apply_condition(change, rules, index, rule, dictionary)
    return _apply_threshold(change, rules, index, rule, dictionary)


def _apply_threshold(
    change: ChangeRequest,
    rules: tuple[Rule, ...],
    index: int,
    rule: Rule,
    dictionary: DataDictionary,
) -> _Applied | _Failure:
    to_payload = change.to_payload
    from_payload = change.from_payload
    if to_payload is None or to_payload.value is None:
        return _Failure(ChangeState.UNSUPPORTED, "replace_threshold requires a new value")
    field, problem = _target_field(rule, to_payload, from_payload, dictionary)
    if problem is not None:
        return problem
    if field is None:
        return _Failure(ChangeState.UNSUPPORTED, "threshold target field is unresolved")
    comparisons = _collect_cmps(rule.condition, field.name)
    if not comparisons:
        return _Failure(
            ChangeState.STALE,
            f"target rule has no comparison on field {field.name!r}",
        )
    selected = comparisons
    if from_payload is not None and from_payload.value is not None:
        old_value = _normalize_payload(from_payload, field)
        if isinstance(old_value, _Failure):
            return old_value
        selected = [cmp for cmp in comparisons if cmp.value == old_value]
        if not selected:
            return _Failure(
                ChangeState.STALE,
                "the change's 'from' value does not match the current base value",
            )
    elif len({cmp.value for cmp in comparisons}) > 1:
        return _Failure(
            ChangeState.UNSUPPORTED,
            f"target rule compares {field.name!r} to more than one threshold; "
            "the change must name the 'from' value",
        )
    new_value = _normalize_payload(to_payload, field)
    if isinstance(new_value, _Failure):
        return new_value
    selected_keys = {(cmp.op, cmp.value) for cmp in selected}

    def transform(cmp: Cmp) -> Cmp:
        if (cmp.op, cmp.value) not in selected_keys:
            return cmp
        return Cmp(field=field.name, op=cmp.op, value=new_value, unit=field.unit)

    condition, count = _replace_cmps(rule.condition, field.name, transform)
    if count == 0:
        return _Failure(ChangeState.STALE, "threshold replacement found no comparison")
    updated = _update_condition(rules, index, condition, rule.rule_ref, change)
    if isinstance(updated, _Failure):
        return updated
    return _Applied(
        rules=updated,
        attribute=(rule.rule_ref, "threshold", field.name),
        effect_key=f"threshold|{field.name}|{new_value}|{field.unit or ''}",
    )


def _apply_operator(
    change: ChangeRequest,
    rules: tuple[Rule, ...],
    index: int,
    rule: Rule,
) -> _Applied | _Failure:
    to_payload = change.to_payload
    from_payload = change.from_payload
    if to_payload is None or to_payload.op is None:
        return _Failure(ChangeState.UNSUPPORTED, "replace_operator requires a new operator")
    if to_payload.op not in _ALLOWED_OPS_VIEW:
        return _Failure(
            ChangeState.UNSUPPORTED, f"operator {to_payload.op!r} is outside the supported set"
        )
    new_op = cast(_Op, to_payload.op)
    field_name = to_payload.field
    if field_name is None:
        fields = {cmp.field for cmp in _collect_cmps(rule.condition, None)}
        if len(fields) != 1:
            return _Failure(
                ChangeState.UNSUPPORTED,
                "replace_operator must name a field when the rule compares several fields",
            )
        field_name = fields.pop()
    comparisons = _collect_cmps(rule.condition, field_name)
    if not comparisons:
        return _Failure(
            ChangeState.STALE,
            f"target rule has no comparison on field {field_name!r}",
        )
    old_op = from_payload.op if from_payload is not None else None
    if old_op is not None:
        selected = [cmp for cmp in comparisons if cmp.op == old_op]
        if not selected:
            return _Failure(
                ChangeState.STALE,
                f"the change's 'from' operator {old_op!r} does not match the base rule",
            )
        old_ops = {old_op}
    else:
        old_ops = {cmp.op for cmp in comparisons}
        if len(old_ops) > 1:
            return _Failure(
                ChangeState.UNSUPPORTED,
                f"target rule compares {field_name!r} with several operators; "
                "the change must name the 'from' operator",
            )

    def transform(cmp: Cmp) -> Cmp:
        if cmp.op not in old_ops:
            return cmp
        return Cmp(field=cmp.field, op=new_op, value=cmp.value, unit=cmp.unit)

    condition, count = _replace_cmps(rule.condition, field_name, transform)
    if count == 0:
        return _Failure(ChangeState.STALE, "operator replacement found no comparison")
    updated = _update_condition(rules, index, condition, rule.rule_ref, change)
    if isinstance(updated, _Failure):
        return updated
    return _Applied(
        rules=updated,
        attribute=(rule.rule_ref, "operator", field_name),
        effect_key=f"operator|{field_name}|{to_payload.op}",
    )


def _apply_destination(
    change: ChangeRequest,
    rules: tuple[Rule, ...],
    index: int,
    rule: Rule,
    dictionary: DataDictionary,
) -> _Applied | _Failure:
    to_payload = change.to_payload
    if to_payload is None or to_payload.destination is None:
        return _Failure(ChangeState.UNSUPPORTED, "replace_destination requires a new destination")
    destination = dictionary.destination(to_payload.destination)
    if destination is None:
        return _Failure(
            ChangeState.UNSUPPORTED,
            f"destination {to_payload.destination!r} is not declared in the data dictionary",
        )
    updated = list(rules)
    updated[index] = rule.model_copy(update={"destination": destination, "origin": _origin(change)})
    return _Applied(
        rules=_with_precedence(tuple(updated)),
        attribute=(rule.rule_ref, "destination"),
        effect_key=f"destination|{destination}",
    )


def _apply_condition(
    change: ChangeRequest,
    rules: tuple[Rule, ...],
    index: int,
    rule: Rule,
    dictionary: DataDictionary,
) -> _Applied | _Failure:
    to_payload = change.to_payload
    if to_payload is None or not to_payload.text:
        return _Failure(ChangeState.UNSUPPORTED, "replace_condition requires condition text")
    try:
        parsed = parse_condition_text(to_payload.text, dictionary)
    except ValueError as exc:
        return _Failure(
            ChangeState.UNSUPPORTED, f"condition text is outside the supported grammar: {exc}"
        )
    normalized = normalize_condition(parsed, dictionary)
    if normalized.expr is None:
        details = "; ".join(problem.message for problem in normalized.problems)
        return _Failure(ChangeState.UNSUPPORTED, f"condition could not be normalized: {details}")
    updated = _update_condition(rules, index, normalized.expr, rule.rule_ref, change)
    if isinstance(updated, _Failure):
        return updated
    return _Applied(
        rules=updated,
        attribute=(rule.rule_ref, "condition"),
        effect_key=f"condition|{canonical_key(normalized.expr)}",
    )


def _apply_add(
    change: ChangeRequest,
    rules: tuple[Rule, ...],
    dictionary: DataDictionary,
) -> _Applied | _Failure:
    to_payload = change.to_payload
    if to_payload is None or not to_payload.text or to_payload.destination is None:
        return _Failure(
            ChangeState.UNSUPPORTED,
            "add_rule requires condition text, a destination and an insertion anchor",
        )
    if not to_payload.after:
        return _Failure(
            ChangeState.UNSUPPORTED,
            "add_rule requires 'after' so the new rule's provenance anchor is known",
        )
    anchor_rule = next(
        (rule for rule in rules if rule.rule_ref == to_payload.after),
        None,
    )
    if anchor_rule is None:
        return _Failure(
            ChangeState.STALE,
            f"insertion anchor {to_payload.after!r} does not exist in the base requirements",
        )
    destination = dictionary.destination(to_payload.destination)
    if destination is None:
        return _Failure(
            ChangeState.UNSUPPORTED,
            f"destination {to_payload.destination!r} is not declared in the data dictionary",
        )
    try:
        parsed = parse_condition_text(to_payload.text, dictionary)
    except ValueError as exc:
        return _Failure(
            ChangeState.UNSUPPORTED, f"condition text is outside the supported grammar: {exc}"
        )
    normalized = normalize_condition(parsed, dictionary)
    if normalized.expr is None:
        details = "; ".join(problem.message for problem in normalized.problems)
        return _Failure(ChangeState.UNSUPPORTED, f"condition could not be normalized: {details}")
    rule_ref = f"change:{change.change_id}"
    if any(rule.rule_ref == rule_ref for rule in rules):
        return _Failure(ChangeState.UNSUPPORTED, f"added rule ref {rule_ref!r} already exists")
    anchor = TextSpan(
        path=anchor_rule.anchor.path,
        line_start=anchor_rule.anchor.line_start,
        line_end=anchor_rule.anchor.line_end,
        col_start=anchor_rule.anchor.col_start,
        col_end=anchor_rule.anchor.col_end,
        excerpt=anchor_rule.anchor.excerpt,
    )
    new_rule = Rule(
        rule_ref=rule_ref,
        anchor=anchor,
        condition=normalized.expr,
        destination=destination,
        precedence=0,
        origin=_origin(change),
        source_text=to_payload.text,
    )
    position = next(i for i, rule in enumerate(rules) if rule.rule_ref == anchor_rule.rule_ref)
    updated = list(rules)
    updated.insert(position + 1, new_rule)
    return _Applied(
        rules=_with_precedence(tuple(updated)),
        attribute=("add",),
        effect_key=f"add|{canonical_key(normalized.expr)}|{destination}",
    )


def _update_condition(
    rules: tuple[Rule, ...],
    index: int,
    new_condition: Expr,
    target_ref: str,
    change: ChangeRequest,
) -> tuple[Rule, ...] | _Failure:
    """Replace a rule condition and its embedding copy inside dependent exceptions."""
    old_condition = rules[index].condition
    updated = list(rules)
    updated[index] = rules[index].model_copy(
        update={"condition": new_condition, "origin": _origin(change)}
    )
    for position, rule in enumerate(updated):
        if rule.exception_of != target_ref:
            continue
        condition = rule.condition
        if (
            not isinstance(condition, And)
            or not condition.items
            or condition.items[0] != old_condition
        ):
            return _Failure(
                ChangeState.UNSUPPORTED,
                f"exception {rule.rule_ref!r} no longer embeds the condition of "
                f"{target_ref!r}; the change cannot be applied consistently",
            )
        updated[position] = rule.model_copy(
            update={"condition": And(items=(new_condition, *condition.items[1:]))}
        )
    return _with_precedence(tuple(updated))


def _origin(change: ChangeRequest) -> str:
    return f"change:{change.change_id}@{change.change_version}"


def _target_field(
    rule: Rule,
    to_payload: ChangePayload,
    from_payload: ChangePayload | None,
    dictionary: DataDictionary,
) -> tuple[FieldDecl | None, _Failure | None]:
    name = to_payload.field
    if name is None and from_payload is not None:
        name = from_payload.field
    if name is None:
        fields = {cmp.field for cmp in _collect_cmps(rule.condition, None)}
        if len(fields) != 1:
            return None, _Failure(
                ChangeState.UNSUPPORTED,
                "replace_threshold must name a field when the rule compares several fields",
            )
        name = fields.pop()
    field = dictionary.field(name)
    if field is None:
        return None, _Failure(
            ChangeState.STALE, f"field {name!r} is not declared in the data dictionary"
        )
    if not field.is_numeric:
        return None, _Failure(ChangeState.UNSUPPORTED, f"field {name!r} is not numeric")
    return field, None


def _normalize_payload(payload: ChangePayload, field: FieldDecl) -> Fraction | _Failure:
    if payload.value is None:
        return _Failure(ChangeState.UNSUPPORTED, "a value is required")
    if field.unit is None:
        if payload.unit is not None:
            return _Failure(
                ChangeState.UNSUPPORTED,
                f"field {field.name!r} declares no unit but {payload.unit!r} was given",
            )
        return Fraction(payload.value)
    if payload.unit is None:
        return _Failure(
            ChangeState.UNSUPPORTED,
            f"unit is required for field {field.name!r} (declared {field.unit!r})",
        )
    try:
        return normalize(payload.value, payload.unit, field.unit)
    except UnitError as exc:
        return _Failure(ChangeState.UNSUPPORTED, str(exc))


def _collect_cmps(expr: Expr, field: str | None) -> list[Cmp]:
    if isinstance(expr, Cmp):
        return [expr] if field is None or expr.field == field else []
    if isinstance(expr, (And, Or)):
        result: list[Cmp] = []
        for item in expr.items:
            result.extend(_collect_cmps(item, field))
        return result
    if isinstance(expr, (NotNode, NotTrue)):
        return _collect_cmps(expr.item, field)
    return []


def _replace_cmps(
    expr: Expr,
    field: str,
    transform: Callable[[Cmp], Cmp],
) -> tuple[Expr, int]:
    if isinstance(expr, Cmp):
        if expr.field != field:
            return expr, 0
        return transform(expr), 1
    if isinstance(expr, (And, Or)):
        items: list[Expr] = []
        total = 0
        for item in expr.items:
            new_item, count = _replace_cmps(item, field, transform)
            items.append(new_item)
            total += count
        if total == 0:
            return expr, 0
        return expr.model_copy(update={"items": tuple(items)}), total
    if isinstance(expr, NotNode):
        inner, count = _replace_cmps(expr.item, field, transform)
        if count == 0:
            return expr, 0
        return NotNode(item=inner), count
    if isinstance(expr, NotTrue):
        inner, count = _replace_cmps(expr.item, field, transform)
        if count == 0:
            return expr, 0
        return NotTrue(item=inner), count
    return expr, 0


def _with_precedence(rules: tuple[Rule, ...]) -> tuple[Rule, ...]:
    return tuple(rule.model_copy(update={"precedence": index}) for index, rule in enumerate(rules))


def _dedupe_scopes(scopes: list[UndeterminedScope]) -> tuple[UndeterminedScope, ...]:
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    unique: list[UndeterminedScope] = []
    for scope in scopes:
        key = (scope.rule_ref, scope.attribute, scope.change_ids)
        if key in seen:
            continue
        seen.add(key)
        unique.append(scope)
    return tuple(unique)


def _dedupe_findings(findings: list[Finding]) -> tuple[Finding, ...]:
    seen: set[str] = set()
    unique: list[Finding] = []
    for finding in findings:
        if finding.finding_id in seen:
            continue
        seen.add(finding.finding_id)
        unique.append(finding)
    return tuple(unique)


__all__ = ["resolve_effective_requirements"]
