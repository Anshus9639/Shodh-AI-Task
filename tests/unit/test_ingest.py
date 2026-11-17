from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from verityflow.config import Limits
from verityflow.ingest.artifacts import parse_artifacts
from verityflow.ingest.lineindex import LineIndex
from verityflow.ingest.loader import BundleLoadError, load_bundle
from verityflow.models.common import TextSpan
from verityflow.models.report import FindingCategory, Severity

DEV = Path(__file__).resolve().parent.parent.parent / "fixtures" / "dev"


def _write_bundle(
    root: Path,
    files: dict[str, tuple[str, str, bytes | str]],
    *,
    approvals: list[dict[str, object]] | None = None,
    requirements: str = "REQ@1.0",
    tamper: dict[str, str] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, object]] = []
    for path, (artifact_id, kind, content) in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        target.write_bytes(data)
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "version": "1.0",
                "kind": kind,
                "path": path,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    if tamper:
        for artifact in artifacts:
            if artifact["path"] in tamper:
                artifact["sha256"] = tamper[artifact["path"]]
    manifest = {
        "bundle_id": root.name,
        "bundle_version": "1.0",
        "review_target": {"requirements": [requirements]},
        "artifacts": artifacts,
        "approvals": approvals or [],
    }
    (root / "bundle.json").write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    return root


def test_clean_bundle_loads_text_json_and_anchors() -> None:
    loaded = load_bundle(DEV / "B01_starter")
    assert loaded.findings == ()
    assert loaded.failed == ()
    assert loaded.unreviewed == ()
    assert loaded.text_of("REQ@1.0") is not None
    assert loaded.json_of("SPEC@1.0") is not None
    index = loaded.index_of("REQ@1.0")
    assert index is not None
    assert index.line_count == 4


def test_hash_mismatch_fails_the_artifact(tmp_path: Path) -> None:
    root = _write_bundle(
        tmp_path / "bundle",
        {"requirements.txt": ("REQ", "requirements", "R1. Missing category -> manual review.\n")},
        tamper={"requirements.txt": "0" * 64},
    )
    loaded = load_bundle(root)
    assert loaded.failed == ("REQ@1.0",)
    assert loaded.text_of("REQ@1.0") is None
    assert loaded.findings[0].category is FindingCategory.PROVENANCE_FAILURE


def test_path_escape_is_rejected(tmp_path: Path) -> None:
    root = _write_bundle(
        tmp_path / "bundle",
        {"../outside.txt": ("REQ", "requirements", "secret")},
    )
    loaded = load_bundle(root)
    assert loaded.failed == ("REQ@1.0",)
    assert "escapes the bundle root" in loaded.findings[0].message


def test_absolute_path_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    root = _write_bundle(
        tmp_path / "bundle",
        {"placeholder.txt": ("X", "requirements", "x")},
        requirements="X@1.0",
    )
    payload = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
    payload["artifacts"][0]["path"] = str(outside)
    (root / "bundle.json").write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_bundle(root)
    assert loaded.failed == ("X@1.0",)
    assert "relative" in loaded.findings[0].message


def test_missing_file_is_reported(tmp_path: Path) -> None:
    root = _write_bundle(tmp_path / "bundle", {"requirements.txt": ("REQ", "requirements", "x")})
    (root / "requirements.txt").unlink()
    loaded = load_bundle(root)
    assert loaded.failed == ("REQ@1.0",)
    assert "does not exist" in loaded.findings[0].message


def test_oversized_artifact_is_disclosed_not_truncated(tmp_path: Path) -> None:
    root = _write_bundle(
        tmp_path / "bundle",
        {"requirements.txt": ("REQ", "requirements", "x" * 5000)},
    )
    loaded = load_bundle(root, limits=Limits(max_artifact_bytes=2000))
    assert loaded.unreviewed == ("REQ@1.0",)
    assert loaded.text_of("REQ@1.0") is None
    finding = next(
        item for item in loaded.findings if item.category is FindingCategory.LIMIT_EXCEEDED
    )
    assert "not loaded" in finding.message


def test_artifact_cap_discloses_remainder(tmp_path: Path) -> None:
    root = _write_bundle(
        tmp_path / "bundle",
        {
            "requirements.txt": ("REQ", "requirements", "x"),
            "second.txt": ("REQ2", "requirements", "y"),
        },
    )
    loaded = load_bundle(root, limits=Limits(max_artifacts=1))
    assert loaded.unreviewed == ("REQ2@1.0",)
    assert any(item.category is FindingCategory.LIMIT_EXCEEDED for item in loaded.findings)


def test_malformed_json_and_utf8_are_reported(tmp_path: Path) -> None:
    root = _write_bundle(
        tmp_path / "json",
        {"spec.json": ("SPEC", "spec", "{not json")},
        requirements="SPEC@1.0",
    )
    loaded = load_bundle(root)
    assert loaded.failed == ("SPEC@1.0",)
    assert "not valid JSON" in loaded.findings[0].message

    root = _write_bundle(
        tmp_path / "utf8",
        {"requirements.txt": ("REQ", "requirements", b"\xff\xfe\x00")},
    )
    loaded = load_bundle(root)
    assert loaded.failed == ("REQ@1.0",)
    assert "UTF-8" in loaded.findings[0].message


def test_bad_manifest_is_a_bundle_error(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "bundle.json").write_text("not json", encoding="utf-8")
    with pytest.raises(BundleLoadError):
        load_bundle(root)
    (root / "bundle.json").write_text("{}", encoding="utf-8")
    with pytest.raises(BundleLoadError):
        load_bundle(root)
    with pytest.raises(BundleLoadError):
        load_bundle(tmp_path / "missing")


def test_line_index_verifies_spans() -> None:
    index = LineIndex("R1. Missing category -> manual review.\nR2. vip -> priority queue.\n")
    first = index.line(1)
    assert first is not None
    assert index.line(0) is None
    assert index.line(3) is None
    span = TextSpan(path="req.txt", line_start=1, line_end=1, col_start=1, col_end=len(first) + 1)
    assert index.check_span(span, expected_path="req.txt") is None
    wrong_path = span.model_copy(update={"path": "other.txt"})
    assert "does not match" in index.check_span(wrong_path, expected_path="req.txt")
    wrong_excerpt = span.model_copy(update={"excerpt": "something else"})
    assert "excerpt" in index.check_span(wrong_excerpt, expected_path="req.txt")
    beyond = span.model_copy(update={"line_end": 9})
    assert "beyond" in index.check_span(beyond, expected_path="req.txt")
    bad_column = span.model_copy(update={"col_end": 999})
    assert "column end" in index.check_span(bad_column, expected_path="req.txt")


def test_typed_artifacts_parse_the_starter_bundle() -> None:
    typed = parse_artifacts(load_bundle(DEV / "B01_starter"))
    assert typed.findings == ()
    assert typed.dictionary is not None
    assert typed.spec is not None
    assert typed.workflow is not None
    assert len(typed.requirements) == 1
    assert len(typed.requirements[0].document.rules) == 4
    assert typed.changes.changes == ()


def test_requirements_without_a_dictionary_are_unreviewed(tmp_path: Path) -> None:
    root = _write_bundle(
        tmp_path / "bundle",
        {"requirements.txt": ("REQ", "requirements", "R1. Missing category -> manual review.\n")},
    )
    typed = parse_artifacts(load_bundle(root))
    assert typed.requirements == ()
    assert any(
        finding.category is FindingCategory.STRUCTURAL and finding.severity is Severity.COVERAGE_GAP
        for finding in typed.findings
    )
