from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HOLDOUT = ROOT / "holdout"
BUNDLES = HOLDOUT / "bundles"
LABELS = HOLDOUT / "labels"
MANIFEST = HOLDOUT / "MANIFEST.sha256"
GENERATION = HOLDOUT / "GENERATION.json"
SRC = ROOT / "src" / "verityflow"
TESTS = ROOT / "tests"


def _manifest_entries() -> dict[Path, str]:
    listed: dict[Path, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, relative = line.partition("  ")
        listed[HOLDOUT / relative] = digest
    return listed


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


def test_manifest_matches_every_reserved_file() -> None:
    listed = _manifest_entries()
    actual = {
        path
        for path in HOLDOUT.rglob("*")
        if path.is_file() and path != MANIFEST and "__pycache__" not in path.parts
    }
    assert set(listed) == actual
    for path, expected in listed.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, f"{path} drifted"


def test_reserved_holdout_is_labelled_and_identified() -> None:
    bundle_names = sorted(path.name for path in BUNDLES.iterdir() if path.is_dir())
    label_names = sorted(path.stem for path in LABELS.glob("*.json"))
    assert len(bundle_names) >= 4
    assert bundle_names == label_names
    generation = json.loads(GENERATION.read_text(encoding="utf-8"))
    assert generation["implementation_checkpoint"] == "04d9ea6"
    assert generation["bundles"] == bundle_names
    assert generation["labels"] == label_names
    assert len(generation["generator_sha256"]) == 64
    assert len(generation["config"]["sha256"]) == 64
    for name in bundle_names:
        label = json.loads((LABELS / f"{name}.json").read_text(encoding="utf-8"))
        assert label["bundle"] == name
        assert label["stage"] == 1
        assert "expected" in label


def test_no_development_test_executes_the_reserved_holdout() -> None:
    for path in TESTS.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path == Path(__file__):
            continue
        if "dev_evidence" in text:
            continue
        if "holdout" in text.lower():
            assert "run_stage1" not in text, f"{path} executes the reserved holdout"


def test_default_pytest_run_excludes_the_holdout_marker() -> None:
    configuration = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "-m 'not holdout'" in configuration
