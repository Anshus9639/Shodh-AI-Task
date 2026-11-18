"""Typed evidence attachment, validation and semantic deduplication.

The catalog is built from a loaded bundle, so every reference is checked against
the exact artifact bytes that were reviewed. Verifiers ask the catalog for a
reference by address (source span, JSON Pointer, AST segment, approval record);
they never compose excerpts themselves. A confirmed finding whose evidence cannot
be resolved is downgraded to a disclosed coverage gap instead of being trusted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from verityflow.ingest.lineindex import LineIndex
from verityflow.ingest.loader import LoadedBundle
from verityflow.ir.policy import Policy
from verityflow.ir.render import render_clause
from verityflow.models.common import TextSpan
from verityflow.models.evidence import AnchorType, EvidenceReference, EvidenceRole
from verityflow.models.manifest import ArtifactEntry, ArtifactKind, BundleManifest
from verityflow.models.report import (
    EvidenceItem,
    Finding,
    FindingCategory,
    Severity,
    UnreviewedScope,
    make_finding,
)

MANIFEST_REF = "bundle.json"

_ROLE_KINDS: dict[EvidenceRole, frozenset[ArtifactKind]] = {
    EvidenceRole.SOURCE_REQUIREMENT: frozenset({ArtifactKind.REQUIREMENTS}),
    EvidenceRole.SPECIFICATION: frozenset({ArtifactKind.SPEC}),
    EvidenceRole.WORKFLOW_BRANCH: frozenset({ArtifactKind.WORKFLOW}),
    EvidenceRole.PYTHON_CODE: frozenset({ArtifactKind.PYTHON}),
    EvidenceRole.APPROVAL_RECORD: frozenset(),
    EvidenceRole.EXECUTION_TRACE: frozenset(),
}

_IMPLEMENTATION_ROLES = frozenset(
    {
        EvidenceRole.SPECIFICATION,
        EvidenceRole.WORKFLOW_BRANCH,
        EvidenceRole.PYTHON_CODE,
        EvidenceRole.APPROVAL_RECORD,
    }
)

_BEHAVIOURAL_CATEGORIES = frozenset(
    {
        FindingCategory.THRESHOLD_CHANGED,
        FindingCategory.WRONG_OPERATOR,
        FindingCategory.WRONG_DESTINATION,
        FindingCategory.PRECEDENCE,
        FindingCategory.SCRIPT_DISAGREEMENT,
        FindingCategory.RUNTIME_ERROR,
        FindingCategory.IMPLEMENTS_UNAPPROVED_CHANGE,
        FindingCategory.UNIT_MISMATCH,
    }
)
_OMISSION_CATEGORIES = frozenset(
    {
        FindingCategory.MISSING_REQUIREMENT,
        FindingCategory.MISSING_BRANCH,
        FindingCategory.MISSING_EXCEPTION,
    }
)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    findings: tuple[Finding, ...]
    unreviewed: tuple[UnreviewedScope, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceCatalog:
    manifest: BundleManifest
    paths: Mapping[str, str]
    hashes: Mapping[str, str]
    texts: Mapping[str, str]
    documents: Mapping[str, object]
    manifest_text: str | None = None
    manifest_json: object | None = None
    manifest_sha256: str | None = None

    @classmethod
    def from_loaded(cls, loaded: LoadedBundle) -> SourceCatalog:
        paths = {entry.ref: entry.path for entry in loaded.manifest.artifacts}
        hashes = {entry.ref: entry.sha256 for entry in loaded.manifest.artifacts}
        manifest_hash = loaded.manifest_sha256
        if manifest_hash is None and loaded.manifest_text is not None:
            manifest_hash = hashlib.sha256(loaded.manifest_text.encode("utf-8")).hexdigest()
        return cls(
            manifest=loaded.manifest,
            paths=paths,
            hashes=hashes,
            texts=loaded.text,
            documents=loaded.json,
            manifest_text=loaded.manifest_text,
            manifest_json=loaded.manifest_json,
            manifest_sha256=manifest_hash,
        )

    # -- reference construction -------------------------------------------------

    def span_reference(
        self, ref: str, span: TextSpan, role: EvidenceRole
    ) -> EvidenceReference | None:
        return self.text_reference(
            ref,
            span.line_start,
            span.line_end,
            role,
            col_start=span.col_start,
            col_end=span.col_end,
            excerpt=span.excerpt,
            anchor_type=(
                AnchorType.SOURCE_LINE
                if span.line_start == span.line_end
                else AnchorType.LINE_RANGE
            ),
        )

    def text_reference(
        self,
        ref: str,
        line_start: int,
        line_end: int,
        role: EvidenceRole,
        *,
        col_start: int | None = None,
        col_end: int | None = None,
        excerpt: str | None = None,
        anchor_type: AnchorType | None = None,
    ) -> EvidenceReference | None:
        entry = self._entry(ref)
        text = self.texts.get(ref)
        if entry is None or text is None:
            return None
        if self._kind_problem(entry, role) is not None:
            return None
        index = LineIndex(text)
        actual = index.excerpt(line_start, line_end)
        if actual is None:
            return None
        if excerpt is not None and excerpt != actual:
            return None
        chosen = excerpt if excerpt is not None else actual
        if not chosen:
            return None
        return EvidenceReference(
            artifact_id=entry.artifact_id,
            artifact_version=entry.version,
            anchor_type=anchor_type
            or (AnchorType.SOURCE_LINE if line_start == line_end else AnchorType.LINE_RANGE),
            anchor_value=f"{entry.path}:{line_start}-{line_end}",
            path=entry.path,
            source_hash=entry.sha256,
            excerpt=chosen,
            evidence_role=role,
            line_start=line_start,
            line_end=line_end,
            col_start=col_start,
            col_end=col_end,
        )

    def named_block_reference(
        self,
        ref: str,
        line_start: int,
        line_end: int,
        role: EvidenceRole,
        *,
        col_start: int | None = None,
        col_end: int | None = None,
        excerpt: str,
    ) -> EvidenceReference | None:
        entry = self._entry(ref)
        text = self.texts.get(ref)
        if entry is None or text is None or not excerpt:
            return None
        if self._kind_problem(entry, role) is not None:
            return None
        region = LineIndex(text).excerpt(line_start, line_end)
        if region is None or excerpt.strip() not in region:
            return None
        return EvidenceReference(
            artifact_id=entry.artifact_id,
            artifact_version=entry.version,
            anchor_type=AnchorType.NAMED_BLOCK,
            anchor_value=f"{entry.path}:{line_start}-{line_end}",
            path=entry.path,
            source_hash=entry.sha256,
            excerpt=excerpt,
            evidence_role=role,
            line_start=line_start,
            line_end=line_end,
            col_start=col_start,
            col_end=col_end,
        )

    def pointer_reference(
        self, ref: str, pointer: str, role: EvidenceRole
    ) -> EvidenceReference | None:
        entry = self._entry(ref)
        document = self._document(ref)
        if entry is None or document is None:
            return None
        if self._kind_problem(entry, role) is not None:
            return None
        try:
            node = resolve_pointer(document, pointer)
        except (ValueError, KeyError, IndexError, TypeError):
            return None
        return EvidenceReference(
            artifact_id=entry.artifact_id,
            artifact_version=entry.version,
            anchor_type=AnchorType.JSON_POINTER,
            anchor_value=pointer,
            path=entry.path,
            source_hash=entry.sha256,
            excerpt=canonical_json(node),
            evidence_role=role,
        )

    def approval_reference(self, approval_id: str) -> EvidenceReference | None:
        if self.manifest_text is None or self.manifest_json is None:
            return None
        index = next(
            (
                position
                for position, record in enumerate(self.manifest.approvals)
                if record.approval_id == approval_id
            ),
            None,
        )
        if index is None:
            return None
        pointer = f"/approvals/{index}"
        try:
            node = resolve_pointer(self.manifest_json, pointer)
        except (ValueError, KeyError, IndexError, TypeError):
            return None
        if not isinstance(node, dict) or node.get("approval_id") != approval_id:
            return None
        return EvidenceReference(
            artifact_id=MANIFEST_REF,
            artifact_version=self.manifest.bundle_version,
            anchor_type=AnchorType.JSON_POINTER,
            anchor_value=pointer,
            path=MANIFEST_REF,
            source_hash=self.manifest_sha256
            or hashlib.sha256(self.manifest_text.encode("utf-8")).hexdigest(),
            excerpt=canonical_json(node),
            evidence_role=EvidenceRole.APPROVAL_RECORD,
        )

    def change_reference(self, change_id: str, change_version: str) -> EvidenceReference | None:
        for entry in self.manifest.entries_of_kind(ArtifactKind.CHANGE_REQUESTS):
            document = self.documents.get(entry.ref)
            if not isinstance(document, dict):
                continue
            changes = document.get("changes")
            if not isinstance(changes, list):
                continue
            for index, node in enumerate(changes):
                if not isinstance(node, dict):
                    continue
                if (
                    node.get("change_id") == change_id
                    and node.get("change_version") == change_version
                ):
                    return EvidenceReference(
                        artifact_id=entry.artifact_id,
                        artifact_version=entry.version,
                        anchor_type=AnchorType.JSON_POINTER,
                        anchor_value=f"/changes/{index}",
                        path=entry.path,
                        source_hash=entry.sha256,
                        excerpt=canonical_json(node),
                        evidence_role=EvidenceRole.APPROVAL_RECORD,
                    )
        return None

    # -- validation ---------------------------------------------------------------

    def validate(self, reference: EvidenceReference) -> str | None:
        bundle = self._bundle_reference(reference)
        entry: ArtifactEntry | None = None
        if bundle:
            text = self.manifest_text
            document: object | None = self.manifest_json
            expected_hash = self.manifest_sha256
            expected_path = MANIFEST_REF
        else:
            entry = self.manifest.entry(reference.artifact_ref)
            if entry is None:
                return f"artifact {reference.artifact_ref!r} is not declared in the bundle"
            text = self.texts.get(reference.artifact_ref)
            document = self.documents.get(reference.artifact_ref)
            expected_hash = entry.sha256
            expected_path = entry.path
        if reference.path != expected_path:
            return (
                f"reference path {reference.path!r} does not match artifact path {expected_path!r}"
            )
        if expected_hash is None or reference.source_hash != expected_hash:
            return f"source hash does not match {reference.artifact_ref!r}"
        if entry is not None:
            kind_problem = self._kind_problem(entry, reference.evidence_role)
            if kind_problem is not None:
                return kind_problem
        if bundle and reference.evidence_role is not EvidenceRole.APPROVAL_RECORD:
            return "bundle.json evidence must be an approval record"
        if reference.anchor_type is AnchorType.JSON_POINTER:
            if document is None:
                return f"artifact {reference.artifact_ref!r} has no parsed JSON document"
            try:
                node = resolve_pointer(document, reference.anchor_value)
            except (ValueError, KeyError, IndexError, TypeError):
                return f"JSON pointer {reference.anchor_value!r} does not resolve"
            if reference.excerpt != canonical_json(node):
                return "excerpt is not the canonical projection of the pointed node"
            return None
        if text is None:
            return f"artifact {reference.artifact_ref!r} has no loaded text"
        if reference.line_start is None or reference.line_end is None:
            return "text evidence has no line range"
        region = LineIndex(text).excerpt(reference.line_start, reference.line_end)
        if region is None:
            return "line range does not exist in the artifact"
        if reference.anchor_type is AnchorType.NAMED_BLOCK:
            if reference.excerpt.strip() not in region:
                return "named block excerpt does not occur at the stated lines"
            return None
        if reference.excerpt != region:
            return "excerpt does not match the artifact lines exactly"
        return None

    def _entry(self, ref: str) -> ArtifactEntry | None:
        return self.manifest.entry(ref)

    def _document(self, ref: str) -> object | None:
        return self.documents.get(ref)

    @staticmethod
    def _kind_problem(entry: ArtifactEntry, role: EvidenceRole) -> str | None:
        allowed = _ROLE_KINDS.get(role)
        if allowed is None:
            return f"unknown evidence role {role.value!r}"
        if not allowed:
            return None
        if entry.kind not in allowed:
            return (
                f"evidence role {role.value} cannot anchor {entry.kind.value} artifact "
                f"{entry.ref!r}"
            )
        return None

    @staticmethod
    def _bundle_reference(reference: EvidenceReference) -> bool:
        return reference.artifact_id == MANIFEST_REF


def canonical_json(node: object) -> str:
    return json.dumps(node, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def resolve_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer {pointer!r}")
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


def attach_source_evidence(
    findings: Iterable[Finding],
    policy: Policy,
    catalog: SourceCatalog,
    requirements_ref: str,
) -> list[Finding]:
    """Attach the requirement-side anchor and a default correction when absent."""
    anchors: dict[str, EvidenceReference] = {}
    for rule in policy.rules:
        reference = catalog.span_reference(
            requirements_ref, rule.anchor, EvidenceRole.SOURCE_REQUIREMENT
        )
        if reference is not None:
            anchors[rule.rule_ref] = reference

    enriched: list[Finding] = []
    for finding in findings:
        reference = anchors.get(finding.rule_ref or "")
        refs = finding.evidence_refs
        if reference is not None and not any(
            item.evidence_role is EvidenceRole.SOURCE_REQUIREMENT
            and item.anchor_value == reference.anchor_value
            for item in refs
        ):
            refs = (reference, *refs)
        recommendation = finding.recommendation
        requirement = policy.rule(finding.rule_ref) if finding.rule_ref is not None else None
        if recommendation is None and requirement is not None and finding.artifact_id:
            recommendation = (
                f"Update {finding.artifact_id} so that the effective requirement "
                f"{requirement.rule_ref} is implemented exactly: "
                f"{render_clause(requirement.condition, requirement.destination)}."
            )
        enriched.append(
            _rebuild(finding, refs, recommendation=recommendation)
            if refs != finding.evidence_refs or recommendation != finding.recommendation
            else finding
        )
    return enriched


def validate_findings(findings: Iterable[Finding], catalog: SourceCatalog) -> ValidationResult:
    """Validate or downgrade findings before they are reported."""
    validated: list[Finding] = []
    scopes: list[UnreviewedScope] = []
    for finding in findings:
        if finding.source != "deterministic":
            if finding.severity is Severity.BLOCKER:
                scopes.append(
                    UnreviewedScope(
                        kind="model",
                        description=(
                            "a non-deterministic model observation cannot establish a "
                            f"blocker; {finding.finding_id} was downgraded"
                        ),
                        rule_refs=(finding.rule_ref,) if finding.rule_ref else (),
                        artifact=finding.artifact_id,
                    )
                )
                validated.append(
                    finding.model_copy(
                        update={
                            "severity": Severity.COVERAGE_GAP,
                            "explanation": _join(
                                finding.explanation,
                                "Model observations are advisory and cannot establish a "
                                "confirmed blocker.",
                            ),
                        }
                    )
                )
            else:
                validated.append(finding)
            continue

        good_refs: list[EvidenceReference] = []
        invalid: list[str] = []
        for reference in finding.evidence_refs:
            problem = catalog.validate(reference)
            if problem is None:
                good_refs.append(reference)
            else:
                invalid.append(
                    f"{reference.evidence_role.value} {reference.anchor_value}: {problem}"
                )

        if finding.severity is not Severity.BLOCKER:
            rebuilt = _rebuild(finding, good_refs) if finding.evidence_refs else finding
            if finding.evidence_refs and not good_refs:
                rebuilt = rebuilt.model_copy(update={"location": None, "evidence": ()})
            validated.append(rebuilt)
            continue

        problems = _confirmed_problems(finding, good_refs, invalid)
        if problems:
            scopes.append(
                UnreviewedScope(
                    kind="evidence",
                    description=(
                        f"{finding.finding_id} ({finding.category.value}) could not be "
                        "confirmed: " + "; ".join(problems)
                    ),
                    rule_refs=(finding.rule_ref,) if finding.rule_ref else (),
                    artifact=finding.artifact_id,
                )
            )
            validated.append(
                make_finding(
                    FindingCategory.MISSING_EVIDENCE,
                    Severity.COVERAGE_GAP,
                    f"unconfirmed {finding.category.value} observation: {finding.message}",
                    rule_ref=finding.rule_ref,
                    artifact_id=finding.artifact_id,
                    artifact_version=finding.artifact_version,
                    semantic=f"missing-evidence|{finding.finding_id}",
                    explanation=(
                        "The observation could not be tied to a valid artifact location "
                        "and evidence reference, so it is disclosed as unreviewed rather "
                        "than reported as a confirmed defect. Reasons: " + "; ".join(problems)
                    ),
                    recommendation=(
                        "Re-run the comparison with an artifact revision whose evidence "
                        "locations resolve, or supply the missing requirement-side and "
                        "implementation-side references."
                    ),
                )
            )
            continue

        validated.append(_rebuild(finding, good_refs))
    return ValidationResult(findings=tuple(validated), unreviewed=tuple(scopes))


def merge_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Merge findings sharing a semantic identity, unioning their evidence."""
    merged: dict[str, Finding] = {}
    order: list[str] = []
    for finding in findings:
        existing = merged.get(finding.finding_id)
        if existing is None:
            merged[finding.finding_id] = finding
            order.append(finding.finding_id)
            continue
        merged[finding.finding_id] = _merge(existing, finding)
    return [merged[key] for key in order]


def _merge(first: Finding, second: Finding) -> Finding:
    refs: list[EvidenceReference] = list(first.evidence_refs)
    seen = {_ref_key(item) for item in refs}
    for item in second.evidence_refs:
        key = _ref_key(item)
        if key in seen:
            continue
        seen.add(key)
        refs.append(item)
    severity = first.severity
    if _severity_rank(second.severity) > _severity_rank(first.severity):
        severity = second.severity
    return first.model_copy(
        update={
            "severity": severity,
            "evidence_refs": tuple(refs),
            "evidence": tuple(
                EvidenceItem(location=item.to_location(), excerpt=item.excerpt) for item in refs
            ),
            "location": refs[0].to_location() if refs else first.location,
            "recommendation": first.recommendation or second.recommendation,
            "explanation": first.explanation or second.explanation,
        }
    )


def _confirmed_problems(
    finding: Finding, refs: list[EvidenceReference], invalid: list[str]
) -> list[str]:
    problems: list[str] = []
    if not finding.artifact_id or not finding.artifact_version:
        problems.append("the finding does not name an implementation artifact revision")
    if not finding.evidence_refs:
        problems.append("the finding carries no evidence reference")
    problems.extend(invalid)
    if not refs:
        problems.append("no evidence reference resolves to the reviewed artifact")
    roles = {item.evidence_role for item in refs}
    if (
        finding.category in _BEHAVIOURAL_CATEGORIES | _OMISSION_CATEGORIES
        and EvidenceRole.SOURCE_REQUIREMENT not in roles
    ):
        problems.append("no requirement-side evidence was attached")
    if finding.category in _BEHAVIOURAL_CATEGORIES:
        if not roles & _IMPLEMENTATION_ROLES:
            problems.append("no implementation-side evidence was attached")
        if not any(item.artifact_id == finding.artifact_id for item in refs):
            problems.append(
                "no evidence reference points into the reported implementation artifact"
            )
    if not (finding.recommendation or "").strip():
        problems.append("the finding has no recommended correction")
    return _unique(problems)


def _rebuild(
    finding: Finding,
    refs: Iterable[EvidenceReference],
    *,
    recommendation: str | None = None,
) -> Finding:
    items = tuple(refs)
    if items:
        location = items[0].to_location()
        evidence = tuple(
            EvidenceItem(location=item.to_location(), excerpt=item.excerpt) for item in items
        )
    else:
        location = None
        evidence = ()
    return finding.model_copy(
        update={
            "evidence_refs": items,
            "location": location,
            "evidence": evidence,
            "recommendation": recommendation
            if recommendation is not None
            else finding.recommendation,
        }
    )


def _ref_key(reference: EvidenceReference) -> tuple[str, str, str, str]:
    return (
        reference.artifact_ref,
        reference.anchor_type.value,
        reference.anchor_value,
        reference.evidence_role.value,
    )


def _severity_rank(severity: Severity) -> int:
    return {
        Severity.INFO: 0,
        Severity.WARNING: 1,
        Severity.COVERAGE_GAP: 2,
        Severity.BLOCKER: 3,
    }[severity]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _join(first: str | None, second: str) -> str:
    return f"{first} {second}" if first else second


__all__ = [
    "MANIFEST_REF",
    "SourceCatalog",
    "ValidationResult",
    "attach_source_evidence",
    "canonical_json",
    "merge_findings",
    "resolve_pointer",
    "validate_findings",
]
