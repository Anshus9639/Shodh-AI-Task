"""Bounded model request construction from a reviewed bundle.

The request carries only what the deterministic review already established:
the status, effective policy summary, findings, coverage, disclosed unreviewed
scope, declared artifact identities and an evidence catalogue of exact
(anchor, excerpt) pairs. Nothing is truncated in place; if the request would be
too large the caller skips model analysis and records why.
"""

from __future__ import annotations

import hashlib
import json

from verityflow.config import ModelSettings
from verityflow.ingest.artifacts import ParsedRequirement, TypedArtifacts, parse_artifacts
from verityflow.ingest.loader import LoadedBundle
from verityflow.model.models import (
    ArtifactSummary,
    CoverageSummary,
    EvidenceSource,
    FindingSummary,
    ModelCacheContext,
    ModelConfiguration,
    ModelFailureCategory,
    ModelRequest,
    RuleSummary,
    UnreviewedSummary,
)
from verityflow.models.manifest import ArtifactEntry, ArtifactKind
from verityflow.requirements.document import RequirementDocument
from verityflow.verify.models import ReviewReport

_MAX_EXCERPT = 2000


class RequestBuildError(RuntimeError):
    """The request cannot be built within the declared limits."""

    def __init__(self, category: ModelFailureCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


def build_model_request(
    loaded: LoadedBundle,
    report: ReviewReport,
    settings: ModelSettings,
) -> ModelRequest:
    manifest = loaded.manifest
    typed = parse_artifacts(loaded)
    document = _requirement_document(typed, report.requirements_ref)
    rules = _rule_summaries(report, document)
    evidence = _evidence_catalogue(loaded)
    available_refs = sorted(
        {rule.rule_ref for rule in rules}
        | {finding.rule_ref for finding in report.findings if finding.rule_ref is not None}
    )
    fields: tuple[str, ...] = ()
    if typed.dictionary is not None:
        fields = tuple(field.name for field in typed.dictionary.fields)
    return ModelRequest(
        bundle_id=manifest.bundle_id,
        bundle_version=manifest.bundle_version,
        deterministic_status=report.status.value,
        deterministic_detail=report.detail,
        exhaustive=report.exhaustive,
        confirmed_candidate_id=report.confirmed_candidate_id,
        applied_changes=report.applied_changes,
        rules=rules,
        findings=tuple(
            FindingSummary(
                finding_id=finding.finding_id,
                severity=finding.severity.value,
                category=finding.category.value,
                message=finding.message,
                rule_ref=finding.rule_ref,
                artifact_id=finding.artifact_id,
                artifact_version=finding.artifact_version,
                explanation=finding.explanation,
            )
            for finding in report.findings
        ),
        coverage=tuple(
            CoverageSummary(
                rule_ref=item.rule_ref,
                destination=item.destination,
                covered=item.covered,
                spec_elements=item.spec_elements,
                workflow_elements=item.workflow_elements,
                code_artifact=item.code_artifact,
                evidence=item.evidence,
            )
            for item in report.coverage
        ),
        unreviewed=tuple(
            UnreviewedSummary(
                kind=scope.kind,
                description=scope.description,
                rule_refs=scope.rule_refs,
                artifact=scope.artifact,
            )
            for scope in report.unreviewed
        ),
        evidence=evidence,
        available_rule_refs=tuple(available_refs),
        available_artifacts=tuple(
            ArtifactSummary(
                artifact_id=entry.artifact_id,
                version=entry.version,
                kind=entry.kind.value,
                sha256=entry.sha256,
            )
            for entry in manifest.artifacts
        ),
        available_fields=fields,
        prompt_version=settings.prompt_version,
        model=ModelConfiguration(
            provider=settings.mode.value,
            model=settings.model,
            prompt_version=settings.prompt_version,
            temperature=settings.temperature,
            max_output_tokens=settings.max_output_tokens,
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
        ),
        cache=_cache_context(manifest.model_dump(mode="json"), report),
    )


def _requirement_document(
    typed: TypedArtifacts, requirements_ref: str | None
) -> RequirementDocument | None:
    parsed: ParsedRequirement | None = None
    if requirements_ref is not None:
        parsed = next(
            (item for item in typed.requirements if item.entry.ref == requirements_ref), None
        )
    if parsed is None and typed.requirements:
        parsed = typed.requirements[0]
    return None if parsed is None else parsed.document


def _rule_summaries(
    report: ReviewReport, document: RequirementDocument | None
) -> tuple[RuleSummary, ...]:
    # Source text is only attached when the deterministic review applied no change
    # request: a changed rule's authoritative text is the effective policy, not the
    # base document line, and inventing a summary would mislead the model.
    changed = bool(report.applied_changes)
    parsed = {}
    if document is not None and not changed:
        parsed = {rule.rule_ref: rule for rule in document.rules}
    return tuple(
        RuleSummary(
            rule_ref=item.rule_ref,
            destination=item.destination,
            source_text=None if changed else _rule_text(parsed.get(item.rule_ref)),
            exception_of=None if changed else _rule_exception(parsed.get(item.rule_ref)),
        )
        for item in report.coverage
    )


def _rule_text(rule: object | None) -> str | None:
    text = getattr(rule, "source_text", None)
    return text if isinstance(text, str) else None


def _rule_exception(rule: object | None) -> str | None:
    value = getattr(rule, "exception_of", None)
    return value if isinstance(value, str) else None


def _cache_context(manifest: dict[str, object], report: ReviewReport) -> ModelCacheContext:
    bundle_digest = _hash_json(manifest)
    approvals = manifest.get("approvals")
    approval_items = approvals if isinstance(approvals, list) else []
    approval_hashes = tuple(
        sorted(_hash_json(item) for item in approval_items if isinstance(item, dict))
    )
    artifacts = manifest.get("artifacts")
    artifact_items = artifacts if isinstance(artifacts, list) else []
    artifact_versions = tuple(
        sorted(
            f"{item['artifact_id']}@{item['version']}={item['sha256']}"
            for item in artifact_items
            if isinstance(item, dict)
        )
    )
    effective = {
        "requirements_ref": report.requirements_ref,
        "confirmed_candidate_id": report.confirmed_candidate_id,
        "applied_changes": list(report.applied_changes),
        "rules": [
            {"rule_ref": item.rule_ref, "destination": item.destination} for item in report.coverage
        ],
    }
    return ModelCacheContext(
        bundle_digest=bundle_digest,
        artifact_versions=artifact_versions,
        approval_hashes=approval_hashes,
        effective_requirements_digest=_hash_json(effective),
        report_digest=_hash_json(report.model_dump(mode="json")),
    )


def _hash_json(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _evidence_catalogue(loaded: LoadedBundle) -> tuple[EvidenceSource, ...]:
    items: list[EvidenceSource] = []
    manifest_version = loaded.manifest.bundle_version
    items.append(
        EvidenceSource(
            artifact_id="bundle.json",
            artifact_version=manifest_version,
            anchor="bundle.json#/bundle_id",
            excerpt=_excerpt(loaded.manifest.bundle_id),
        )
    )
    items.append(
        EvidenceSource(
            artifact_id="bundle.json",
            artifact_version=manifest_version,
            anchor="bundle.json#/bundle_version",
            excerpt=_excerpt(manifest_version),
        )
    )
    manifest_document = {
        "artifacts": loaded.manifest.model_dump(mode="json")["artifacts"],
        "approvals": loaded.manifest.model_dump(mode="json")["approvals"],
    }
    for key in ("artifacts", "approvals"):
        for index, value in enumerate(manifest_document[key]):
            items.append(
                EvidenceSource(
                    artifact_id="bundle.json",
                    artifact_version=loaded.manifest.bundle_version,
                    anchor=f"bundle.json#/{key}/{index}",
                    excerpt=_excerpt(_canonical(value)),
                )
            )
    for entry in loaded.manifest.artifacts:
        text = loaded.text_of(entry.ref)
        if text is None:
            continue
        if entry.kind is ArtifactKind.REQUIREMENTS or entry.kind is ArtifactKind.PYTHON:
            items.extend(_line_evidence(entry, text))
            continue
        document = loaded.json_of(entry.ref)
        if document is None:
            items.extend(_line_evidence(entry, text))
            continue
        items.extend(_json_evidence(entry, document))
    return tuple(items)


def _line_evidence(entry: ArtifactEntry, text: str) -> list[EvidenceSource]:
    items: list[EvidenceSource] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        items.append(
            EvidenceSource(
                artifact_id=entry.artifact_id,
                artifact_version=entry.version,
                anchor=f"{entry.path}#L{number}",
                excerpt=_excerpt(line),
            )
        )
    return items


def _json_evidence(entry: ArtifactEntry, document: object) -> list[EvidenceSource]:
    if not isinstance(document, dict):
        return []
    items: list[EvidenceSource] = []
    for key, value in document.items():
        if isinstance(value, list):
            for index, item in enumerate(value):
                items.append(
                    EvidenceSource(
                        artifact_id=entry.artifact_id,
                        artifact_version=entry.version,
                        anchor=f"{entry.path}#/{key}/{index}",
                        excerpt=_excerpt(_canonical(item)),
                    )
                )
        elif isinstance(value, dict):
            for sub_key, sub_value in value.items():
                items.append(
                    EvidenceSource(
                        artifact_id=entry.artifact_id,
                        artifact_version=entry.version,
                        anchor=f"{entry.path}#/{key}/{sub_key}",
                        excerpt=_excerpt(_canonical(sub_value)),
                    )
                )
    return items


def _excerpt(text: str) -> str:
    if len(text) > _MAX_EXCERPT:
        raise RequestBuildError(
            ModelFailureCategory.REQUEST_TOO_LARGE,
            f"an evidence excerpt in the bundle is {len(text)} characters, exceeding the "
            f"{_MAX_EXCERPT}-character quotation limit; model analysis is skipped rather "
            "than truncating evidence",
        )
    return text


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


__all__ = ["RequestBuildError", "build_model_request"]
