from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer
from pydantic import BaseModel, ValidationError

from verityflow import __version__
from verityflow.approvals.resolver import resolve_effective_requirements
from verityflow.config import Config, ConfigError, load_config
from verityflow.ingest.artifacts import TypedArtifacts, parse_artifacts
from verityflow.ingest.loader import BundleLoadError, LoadedBundle, load_bundle
from verityflow.mapping.extract import (
    extract_requirements,
    extract_spec,
    extract_workflow,
)
from verityflow.mapping.matcher import map_to_elements
from verityflow.models.common import ArtifactRef
from verityflow.models.dictionary import DataDictionary
from verityflow.models.manifest import ArtifactEntry, ArtifactKind
from verityflow.models.report import Finding, FindingCategory, Severity, make_finding
from verityflow.requirements.compile import compile_document
from verityflow.requirements.parser import parse_requirements

app = typer.Typer(no_args_is_help=True, add_completion=False)

_EXIT_OK = 0
_EXIT_FINDING = 1
_EXIT_INPUT = 2

_GAP_SEVERITIES = frozenset({Severity.BLOCKER, Severity.COVERAGE_GAP})


@app.callback()
def main() -> None:
    """VerityFlow - AI-assisted workflow compliance reviewer."""


@app.command()
def version() -> None:
    """Print the tool version."""
    typer.echo(__version__)


@app.command()
def parse(
    requirements: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Requirement document to parse.",
        ),
    ],
    dictionary: Annotated[
        Path,
        typer.Option("--dictionary", "-d", help="Data dictionary JSON."),
    ],
) -> None:
    """Parse a requirement document and compile its ordered first-match policy."""
    data_dictionary = _load_dictionary(dictionary)
    try:
        text = requirements.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _fail(f"cannot read requirements {requirements}: {exc}")
    document = parse_requirements(text, str(requirements), data_dictionary)
    policy, findings = compile_document(
        document, artifact=ArtifactRef(artifact_id=requirements.stem, version="local")
    )
    payload = {
        "requirements": str(requirements),
        "lines": document.line_count,
        "rules": [_dump(rule) for rule in policy.rules],
        "findings": [_dump(finding) for finding in findings],
        "coverage_gap": _has_gap(findings),
    }
    _emit(payload)
    raise typer.Exit(_exit_for(findings))


@app.command()
def resolve(
    bundle: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Bundle directory containing bundle.json.",
        ),
    ],
    requirements_ref: Annotated[
        str | None,
        typer.Option("--requirements", help="Requirement artifact ref ID@version."),
    ] = None,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="TOML configuration file."),
    ] = None,
) -> None:
    """Resolve approvals into the effective requirements policy for a bundle."""
    config = _load_config(config_path)
    loaded = _open_bundle(bundle, config)
    typed = parse_artifacts(loaded)
    findings = [*loaded.findings, *typed.findings]
    if typed.dictionary is None:
        _emit({"bundle": str(bundle), "findings": [_dump(item) for item in findings]})
        raise typer.Exit(_EXIT_FINDING)
    entry = _select_requirements(loaded, typed, requirements_ref, findings)
    if entry is None:
        _emit({"bundle": str(bundle), "findings": [_dump(item) for item in findings]})
        raise typer.Exit(_EXIT_FINDING)
    parsed = next(
        (item for item in typed.requirements if item.entry.ref == entry.ref),
        None,
    )
    if parsed is None:
        findings.append(_unloaded_requirements(entry))
        _emit({"bundle": str(bundle), "findings": [_dump(item) for item in findings]})
        raise typer.Exit(_EXIT_FINDING)
    base_policy, compile_findings = compile_document(
        parsed.document,
        artifact=ArtifactRef(artifact_id=entry.artifact_id, version=entry.version),
    )
    findings.extend(compile_findings)
    effective = resolve_effective_requirements(
        base_policy,
        manifest=loaded.manifest,
        changes=typed.changes,
        dictionary=typed.dictionary,
        config=config,
        requirements_ref=entry.ref,
        changes_ref=_first_ref(loaded, ArtifactKind.CHANGE_REQUESTS),
    )
    findings.extend(effective.findings)
    confirmed = effective.confirmed()
    payload = {
        "bundle": loaded.manifest.bundle_id,
        "bundle_version": loaded.manifest.bundle_version,
        "requirements_ref": entry.ref,
        "base_policy": _dump(base_policy),
        "effective_requirements": _dump(effective),
        "confirmed_candidate_id": confirmed.candidate_id if confirmed is not None else None,
        "findings": [_dump(item) for item in findings],
    }
    _emit(payload)
    raise typer.Exit(_exit_for(findings))


@app.command(name="map")
def map_artifacts(
    bundle: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            dir_okay=True,
            readable=True,
            help="Bundle directory containing bundle.json.",
        ),
    ],
    artifact: Annotated[
        str,
        typer.Option(
            "--artifact",
            help="Artifact to map: spec, workflow, or all.",
        ),
    ] = "all",
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="TOML configuration file."),
    ] = None,
) -> None:
    """Infer semantic correspondence from requirements to spec and workflow."""
    if artifact not in {"spec", "workflow", "all"}:
        _fail("--artifact must be spec, workflow, or all", code=_EXIT_INPUT)
    config = _load_config(config_path)
    loaded = _open_bundle(bundle, config)
    typed = parse_artifacts(loaded)
    findings = [*loaded.findings, *typed.findings]
    dictionary = typed.dictionary
    if dictionary is None:
        _emit({"bundle": str(bundle), "findings": [_dump(item) for item in findings]})
        raise typer.Exit(_EXIT_FINDING)
    entry = _select_requirements(loaded, typed, None, findings)
    parsed = (
        next((item for item in typed.requirements if item.entry.ref == entry.ref), None)
        if entry is not None
        else None
    )
    if entry is None or parsed is None:
        _emit({"bundle": str(bundle), "findings": [_dump(item) for item in findings]})
        raise typer.Exit(_EXIT_FINDING)
    base_policy, compile_findings = compile_document(
        parsed.document,
        artifact=ArtifactRef(artifact_id=entry.artifact_id, version=entry.version),
    )
    findings.extend(compile_findings)
    requirements = extract_requirements(base_policy).elements
    findings.extend(_deferred_code(loaded))

    mappings: dict[str, Any] = {}
    if artifact in {"spec", "all"} and typed.spec is not None:
        spec_entry = _first_entry(loaded, ArtifactKind.SPEC)
        extraction = extract_spec(
            typed.spec,
            dictionary,
            ArtifactRef(artifact_id=spec_entry.artifact_id, version=spec_entry.version),
        )
        result = map_to_elements(
            requirements,
            extraction,
            dictionary,
            artifact="spec",
            config=config,
            hints=_spec_hints(typed),
            artifact_ref=ArtifactRef(
                artifact_id=spec_entry.artifact_id, version=spec_entry.version
            ),
            trace_ref=_trace_ref(loaded),
        )
        mappings["spec"] = _dump(result)
        findings.extend(result.findings)
    if artifact in {"workflow", "all"} and typed.workflow is not None:
        workflow_entry = _first_entry(loaded, ArtifactKind.WORKFLOW)
        extraction = extract_workflow(
            typed.workflow,
            dictionary,
            ArtifactRef(artifact_id=workflow_entry.artifact_id, version=workflow_entry.version),
        )
        result = map_to_elements(
            requirements,
            extraction,
            dictionary,
            artifact="workflow",
            config=config,
            hints=_workflow_hints(typed),
            artifact_ref=ArtifactRef(
                artifact_id=workflow_entry.artifact_id, version=workflow_entry.version
            ),
            trace_ref=_trace_ref(loaded),
        )
        mappings["workflow"] = _dump(result)
        findings.extend(result.findings)
    payload = {
        "bundle": loaded.manifest.bundle_id,
        "bundle_version": loaded.manifest.bundle_version,
        "requirements_ref": entry.ref,
        "mappings": mappings,
        "findings": [_dump(item) for item in findings],
    }
    _emit(payload)
    raise typer.Exit(_exit_for(findings))


def _load_dictionary(path: Path) -> DataDictionary:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot read data dictionary {path}: {exc}")
    try:
        return DataDictionary.model_validate(payload)
    except ValidationError as exc:
        _fail(f"data dictionary {path} does not match its schema: {exc}")


def _load_config(path: Path | None) -> Config:
    try:
        return load_config(path)
    except ConfigError as exc:
        _fail(str(exc))


def _open_bundle(path: Path, config: Config) -> LoadedBundle:
    try:
        return load_bundle(path, limits=config.limits)
    except BundleLoadError as exc:
        _fail(str(exc), code=_EXIT_INPUT)


def _select_requirements(
    loaded: LoadedBundle,
    typed: TypedArtifacts,
    requested: str | None,
    findings: list[Finding],
) -> ArtifactEntry | None:
    if requested is not None:
        entry = loaded.manifest.entry(requested)
        if entry is None or entry.kind is not ArtifactKind.REQUIREMENTS:
            findings.append(
                _structure_finding(
                    loaded,
                    f"requested requirement artifact {requested!r} is not declared "
                    "as a requirement artifact",
                )
            )
            return None
        return entry
    refs = loaded.manifest.review_target.requirements
    entry = loaded.manifest.entry(refs[0])
    if entry is None:
        findings.append(
            _structure_finding(
                loaded,
                f"review target requirement {refs[0]!r} is not declared",
            )
        )
    return entry


def _first_entry(loaded: LoadedBundle, kind: ArtifactKind) -> ArtifactEntry:
    entries = loaded.manifest.entries_of_kind(kind)
    if not entries:
        raise AssertionError(f"no {kind.value} artifact was parsed")
    return entries[0]


def _first_ref(loaded: LoadedBundle, kind: ArtifactKind) -> str | None:
    entries = loaded.manifest.entries_of_kind(kind)
    return entries[0].ref if entries else None


def _trace_ref(loaded: LoadedBundle) -> ArtifactRef | None:
    entries = loaded.manifest.entries_of_kind(ArtifactKind.TRACE_MAP)
    if not entries:
        return None
    return ArtifactRef(artifact_id=entries[0].artifact_id, version=entries[0].version)


def _spec_hints(typed: TypedArtifacts) -> dict[str, tuple[str, ...]] | None:
    return typed.trace.requirement_to_spec if typed.trace is not None else None


def _workflow_hints(typed: TypedArtifacts) -> dict[str, tuple[str, ...]] | None:
    return typed.trace.requirement_to_workflow if typed.trace is not None else None


def _deferred_code(loaded: LoadedBundle) -> list[Finding]:
    entries = loaded.manifest.entries_of_kind(ArtifactKind.PYTHON)
    return [
        make_finding(
            FindingCategory.DEFERRED_DESCRIPTOR,
            Severity.COVERAGE_GAP,
            f"python artifact {entry.ref} has no Stage 1 semantic descriptor; its "
            "correspondence to the requirements is unreviewed",
            artifact_id=entry.artifact_id,
            artifact_version=entry.version,
        )
        for entry in entries
    ]


def _unloaded_requirements(entry: ArtifactEntry) -> Finding:
    return make_finding(
        FindingCategory.PROVENANCE_FAILURE,
        Severity.COVERAGE_GAP,
        f"requirement artifact {entry.ref} was not loaded; coverage is unreviewed",
        artifact_id=entry.artifact_id,
        artifact_version=entry.version,
    )


def _structure_finding(loaded: LoadedBundle, message: str) -> Finding:
    return make_finding(
        FindingCategory.STRUCTURAL,
        Severity.COVERAGE_GAP,
        message,
        artifact_id="bundle.json",
        artifact_version=loaded.manifest.bundle_version,
    )


def _dump(model: BaseModel) -> Any:
    return model.model_dump(mode="json")


def _emit(payload: dict[str, Any]) -> None:
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


def _has_gap(findings: Iterable[Finding]) -> bool:
    return any(finding.severity in _GAP_SEVERITIES for finding in findings)


def _exit_for(findings: Iterable[Finding]) -> int:
    return _EXIT_FINDING if _has_gap(findings) else _EXIT_OK


def _fail(message: str, *, code: int = _EXIT_INPUT) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code)


if __name__ == "__main__":
    app()
