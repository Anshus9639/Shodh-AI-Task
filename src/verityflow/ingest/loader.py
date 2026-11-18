from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from verityflow.config import Limits
from verityflow.ingest.lineindex import LineIndex
from verityflow.models.manifest import ArtifactEntry, ArtifactKind, BundleManifest
from verityflow.models.report import Finding, FindingCategory, Severity, make_finding

_JSON_KINDS = frozenset(
    {
        ArtifactKind.DATA_DICTIONARY,
        ArtifactKind.CHANGE_REQUESTS,
        ArtifactKind.TRACE_MAP,
        ArtifactKind.SPEC,
        ArtifactKind.WORKFLOW,
    }
)


class BundleLoadError(ValueError):
    """The bundle itself cannot be opened as a manifest (not an artifact-level defect)."""


@dataclass(frozen=True, slots=True)
class LoadedBundle:
    root: Path
    manifest: BundleManifest
    text: dict[str, str] = field(default_factory=dict)
    json: dict[str, object] = field(default_factory=dict)
    findings: tuple[Finding, ...] = ()
    failed: tuple[str, ...] = ()
    unreviewed: tuple[str, ...] = ()
    manifest_text: str | None = None
    manifest_json: dict[str, object] | None = None
    manifest_sha256: str | None = None

    def text_of(self, ref: str) -> str | None:
        return self.text.get(ref)

    def json_of(self, ref: str) -> object | None:
        return self.json.get(ref)

    def index_of(self, ref: str) -> LineIndex | None:
        content = self.text.get(ref)
        return LineIndex(content) if content is not None else None


def load_bundle(
    root: Path,
    *,
    manifest_name: str = "bundle.json",
    limits: Limits | None = None,
) -> LoadedBundle:
    if not root.is_dir():
        raise BundleLoadError(f"bundle root {root} is not a directory")
    active_limits = limits or Limits()
    root = root.resolve()
    manifest_path = _resolve_inside(root, manifest_name)
    if not manifest_path.is_file():
        raise BundleLoadError(f"manifest {manifest_name!r} not found in {root}")
    size = manifest_path.stat().st_size
    if size > active_limits.max_artifact_bytes:
        raise BundleLoadError(
            f"manifest is {size} bytes, exceeding max_artifact_bytes="
            f"{active_limits.max_artifact_bytes}"
        )
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest_text = manifest_bytes.decode("utf-8")
        payload = json.loads(manifest_text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleLoadError(f"manifest is not valid UTF-8 JSON: {exc}") from exc
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    try:
        manifest = BundleManifest.model_validate(payload)
    except ValidationError as exc:
        raise BundleLoadError(f"manifest does not satisfy the bundle schema: {exc}") from exc

    findings: list[Finding] = []
    text: dict[str, str] = {}
    documents: dict[str, object] = {}
    failed: list[str] = []
    unreviewed: list[str] = []

    entries = list(manifest.artifacts)
    if len(entries) > active_limits.max_artifacts:
        unloaded = entries[active_limits.max_artifacts :]
        unreviewed.extend(entry.ref for entry in unloaded)
        findings.append(
            make_finding(
                FindingCategory.LIMIT_EXCEEDED,
                Severity.COVERAGE_GAP,
                f"bundle declares {len(entries)} artifacts, exceeding max_artifacts="
                f"{active_limits.max_artifacts}; unloaded: "
                f"{', '.join(entry.ref for entry in unloaded)}",
                artifact_id="bundle.json",
                artifact_version=manifest.bundle_version,
            )
        )
        entries = entries[: active_limits.max_artifacts]

    for entry in entries:
        try:
            path = _resolve_inside(root, entry.path)
        except BundleLoadError as exc:
            failed.append(entry.ref)
            findings.append(_entry_finding(entry, FindingCategory.PROVENANCE_FAILURE, str(exc)))
            continue
        if not path.is_file():
            failed.append(entry.ref)
            findings.append(
                _entry_finding(
                    entry,
                    FindingCategory.PROVENANCE_FAILURE,
                    f"artifact path {entry.path!r} does not exist inside the bundle",
                )
            )
            continue
        size = path.stat().st_size
        if size > active_limits.max_artifact_bytes:
            unreviewed.append(entry.ref)
            findings.append(
                _entry_finding(
                    entry,
                    FindingCategory.LIMIT_EXCEEDED,
                    f"artifact is {size} bytes, exceeding max_artifact_bytes="
                    f"{active_limits.max_artifact_bytes}; it was not loaded and remains "
                    "unreviewed",
                )
            )
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry.sha256:
            failed.append(entry.ref)
            findings.append(
                _entry_finding(
                    entry,
                    FindingCategory.PROVENANCE_FAILURE,
                    f"sha256 mismatch: manifest declares {entry.sha256}, file has {digest}",
                )
            )
            continue
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            failed.append(entry.ref)
            findings.append(
                _entry_finding(
                    entry,
                    FindingCategory.PROVENANCE_FAILURE,
                    f"artifact is not valid UTF-8 text: {exc}",
                )
            )
            continue
        text[entry.ref] = content
        if entry.kind in _JSON_KINDS:
            try:
                documents[entry.ref] = json.loads(content)
            except json.JSONDecodeError as exc:
                failed.append(entry.ref)
                findings.append(
                    _entry_finding(
                        entry,
                        FindingCategory.PROVENANCE_FAILURE,
                        f"artifact is not valid JSON: {exc}",
                    )
                )

    return LoadedBundle(
        root=root,
        manifest=manifest,
        text=text,
        json=documents,
        findings=tuple(findings),
        failed=tuple(failed),
        unreviewed=tuple(unreviewed),
        manifest_text=manifest_text,
        manifest_json=payload,
        manifest_sha256=manifest_sha256,
    )


def _resolve_inside(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or candidate.drive or candidate.root:
        raise BundleLoadError(f"artifact path {relative!r} must be relative to the bundle root")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise BundleLoadError(f"artifact path {relative!r} escapes the bundle root")
    return resolved


def _entry_finding(entry: ArtifactEntry, category: FindingCategory, message: str) -> Finding:
    return make_finding(
        category,
        Severity.COVERAGE_GAP,
        message,
        artifact_id=entry.artifact_id,
        artifact_version=entry.version,
    )


__all__ = ["BundleLoadError", "LoadedBundle", "load_bundle"]
