from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HOLDOUT = ROOT / "holdout"
BUNDLES = HOLDOUT / "bundles"
LABELS = HOLDOUT / "labels"
MANIFEST = HOLDOUT / "MANIFEST.sha256"
SRC = ROOT / "src" / "verityflow"


def test_labels_live_outside_the_bundles_they_grade() -> None:
    assert LABELS.is_dir() and BUNDLES.is_dir()
    for label in LABELS.glob("*.json"):
        assert BUNDLES not in label.parents


def test_no_bundle_directory_contains_expected_answers() -> None:
    for path in BUNDLES.rglob("*"):
        if not path.is_file():
            continue
        assert "label" not in path.name.lower()
        assert "expected" not in path.name.lower()
        if path.suffix == ".json":
            text = path.read_text(encoding="utf-8")
            assert '"expected"' not in text
            assert '"holdout"' not in text


def test_source_never_reads_holdout_labels() -> None:
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        assert "holdout" not in text, f"{path} references the holdout set"
        assert "manifest.sha256" not in text, f"{path} references the holdout manifest"


def test_manifest_matches_every_bundle_file() -> None:
    listed: dict[Path, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, relative = line.partition("  ")
        listed[HOLDOUT / relative] = digest
    actual = {path for path in BUNDLES.rglob("*") if path.is_file()}
    assert set(listed) == actual
    for path, expected in listed.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, f"{path} drifted"


def test_default_pytest_run_excludes_the_holdout_marker() -> None:
    configuration = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "-m 'not holdout'" in configuration
