from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HOLDOUT = ROOT / "holdout_stage2"
BUNDLES = HOLDOUT / "bundles"
LABELS = HOLDOUT / "labels"
MANIFEST = HOLDOUT / "MANIFEST.sha256"
GENERATION = HOLDOUT / "GENERATION.json"
GENERATOR = ROOT / "scripts" / "build_stage2_holdout.py"
CONFIG = ROOT / "configs" / "default.toml"
TESTS = ROOT / "tests"
STAGE1_MANIFEST = ROOT / "holdout" / "MANIFEST.sha256"
STAGE1_MANIFEST_SHA256 = "e10de058ceb51586f16c657c75e4fd3b4fd9cbded6f76beda9a4e0c648ba6f41"


def _manifest_entries() -> dict[Path, str]:
    listed: dict[Path, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, relative = line.partition("  ")
        listed[HOLDOUT / relative] = digest
    return listed


def _text_digest(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def _generation() -> dict[str, object]:
    return json.loads(GENERATION.read_text(encoding="utf-8"))


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


def test_labels_live_outside_the_bundles_they_grade() -> None:
    bundle_names = sorted(path.name for path in BUNDLES.iterdir() if path.is_dir())
    label_names = sorted(path.stem for path in LABELS.glob("*.json"))
    assert bundle_names == label_names
    for label in LABELS.glob("*.json"):
        assert BUNDLES not in label.parents
    for path in BUNDLES.rglob("*"):
        if not path.is_file():
            continue
        assert "label" not in path.name.lower()
        assert "expected" not in path.name.lower()
        if path.suffix == ".json":
            text = path.read_text(encoding="utf-8")
            assert '"expected"' not in text
            assert '"holdout_id"' not in text


def test_labels_are_frozen_stage2_expectations() -> None:
    for name in sorted(path.name for path in BUNDLES.iterdir() if path.is_dir()):
        label = json.loads((LABELS / f"{name}.json").read_text(encoding="utf-8"))
        assert label["bundle"] == name
        assert label["stage"] == 2
        assert label["holdout_id"] == "stage2_semantic_v1"
        assert "derivation" in label
        assert set(label["expected"]) >= {"status", "finding_categories"}


def test_generation_records_seed_and_checkpoint_provenance() -> None:
    generation = _generation()
    bundle_names = sorted(path.name for path in BUNDLES.iterdir() if path.is_dir())
    label_names = sorted(path.stem for path in LABELS.glob("*.json"))
    assert generation["bundles"] == bundle_names
    assert generation["labels"] == label_names
    assert generation["seed"] == 20261117
    assert generation["generator_sha256"] == _text_digest(GENERATOR)
    assert generation["config"]["sha256"] == _text_digest(CONFIG)
    assert generation["provenance"]["baseline_implementation_checkpoint"] == "04d9ea6"
    assert generation["provenance"]["fixture_generation_base_commit"] == "9a420cd"
    assert set(generation["phrase_assignment"]) == set(bundle_names)


def test_stage1_sealed_manifest_is_unchanged() -> None:
    digest = hashlib.sha256(STAGE1_MANIFEST.read_bytes()).hexdigest()
    assert digest == STAGE1_MANIFEST_SHA256


def test_no_development_test_references_the_stage2_holdout() -> None:
    for path in TESTS.rglob("*.py"):
        if path == Path(__file__) or "__pycache__" in path.parts:
            continue
        if "dev_evidence" in path.read_text(encoding="utf-8"):
            continue
        assert "holdout_stage2" not in path.read_text(encoding="utf-8"), (
            f"{path} references the sealed Stage 2 holdout"
        )
