"""Freeze the Stage 4 development dataset and record provenance.

Writes ``evaluation/MANIFEST.sha256`` (every development bundle file, every
label and the oracle) and ``evaluation/DATASET.json`` (per-bundle provenance).
Sealed Stage 1 and Stage 2 sets are referenced through their existing
manifests; their bundle bytes and labels are never read here.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVALUATION = ROOT / "evaluation"
CASES = EVALUATION / "cases"

DEV_BUNDLE_DIRS = ("fixtures/dev", "fixtures/dev_evidence/holdout_v1/bundles")
LABEL_PROVENANCE = "independent oracle plus manual reasoning; reviewer output not used"
FREEZE_NOTE = "first commit that adds evaluation/ (recorded in docs/evaluation_protocol.md)"
EVAL_BUNDLE_DIR = "evaluation/bundles"

SEALED_SETS = (
    {
        "set_id": "stage1_final_v1",
        "root": "holdout",
        "suite": "sealed_stage1",
        "seed": None,
    },
    {
        "set_id": "stage2_semantic_v1",
        "root": "holdout_stage2",
        "suite": "sealed_stage2",
        "seed": 20261117,
    },
)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bundle_files(path: Path) -> list[Path]:
    return sorted(item for item in path.rglob("*") if item.is_file())


def manifest_lines(paths: list[Path]) -> list[str]:
    return [f"{file_hash(path)}  {path.relative_to(ROOT).as_posix()}" for path in paths]


def parse_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split("  ", 1)
        entries[name] = digest
    return entries


def bundle_content_hash(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def generation_metadata(root: str) -> dict[str, object]:
    data = json.loads((ROOT / root / "GENERATION.json").read_text(encoding="utf-8"))
    return {
        "generator": data.get("generated_by"),
        "generator_sha256": data.get("generator_sha256"),
        "seed": data.get("seed"),
        "config_sha256": (data.get("config") or {}).get("sha256"),
        "implementation_checkpoint": (data.get("provenance") or {}).get(
            "baseline_implementation_checkpoint"
        )
        or data.get("implementation_checkpoint"),
        "policy": data.get("policy"),
    }


def freeze_commit() -> str:
    if len(sys.argv) == 3 and sys.argv[1] == "--freeze-commit":
        value = sys.argv[2]
        if not re.fullmatch(r"[0-9a-f]{40}", value):
            raise SystemExit(f"freeze commit {value!r} is not a full SHA-1")
        return value
    if len(sys.argv) != 1:
        raise SystemExit("usage: build_dataset_manifest.py [--freeze-commit <sha1>]")
    return FREEZE_NOTE


def main() -> int:
    freeze = freeze_commit()
    labels = {path.stem: path for path in sorted(CASES.glob("*.json"))}
    if not labels:
        raise SystemExit("no labels found; run scripts/build_eval_labels.py first")

    case_data = {
        name: json.loads(path.read_text(encoding="utf-8")) for name, path in labels.items()
    }

    development: list[dict[str, object]] = []
    dev_files: list[Path] = []
    for name, data in case_data.items():
        bundle_dir = ROOT / str(data["bundle_path"])
        files = bundle_files(bundle_dir)
        dev_files.extend(files)
        lines = manifest_lines(files)
        development.append(
            {
                "bundle_id": name,
                "suite": data["suite"],
                "bundle_path": data["bundle_path"],
                "bundle_content_hash": bundle_content_hash(lines),
                "bundle_file_hashes": {
                    line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in lines
                },
                "expected_outcome_reference": f"evaluation/cases/{name}.json",
                "expected_label_sha256": file_hash(labels[name]),
                "expected_label_provenance": LABEL_PROVENANCE,
                "source_evidence_reference": f"evaluation/cases/{name}.json#source_evidence",
                "generation_seed": None,
                "generation_config_sha256": file_hash(ROOT / "configs" / "default.toml"),
                "generator_sha256": (
                    file_hash(ROOT / "scripts" / "build_stage4_eval_fixtures.py")
                    if name.startswith("E")
                    else file_hash(ROOT / "scripts" / "build_fixtures.py")
                ),
                "implementation_checkpoint": "b63da40",
                "dataset_freeze_commit": freeze,
            }
        )

    sealed: list[dict[str, object]] = []
    for spec in SEALED_SETS:
        root = str(spec["root"])
        metadata = generation_metadata(root)
        manifest = parse_manifest(ROOT / root / "MANIFEST.sha256")
        bundle_names = sorted(
            {
                name.split("/")[1]
                for name in manifest
                if name.startswith("bundles/") and name.count("/") >= 2
            }
        )
        for bundle in bundle_names:
            lines = sorted(
                f"{manifest[name]}  {root}/{name}"
                for name in manifest
                if name.startswith(f"bundles/{bundle}/")
            )
            label_name = f"labels/{bundle}.json"
            sealed.append(
                {
                    "bundle_id": bundle,
                    "suite": spec["suite"],
                    "bundle_path": f"{root}/bundles/{bundle}",
                    "bundle_content_hash": bundle_content_hash(lines),
                    "bundle_file_hashes": {
                        f"{root}/{name}": manifest[name]
                        for name in manifest
                        if name.startswith(f"bundles/{bundle}/")
                    },
                    "expected_outcome_reference": f"{root}/{label_name}",
                    "expected_label_sha256": manifest.get(label_name),
                    "expected_label_provenance": metadata["policy"],
                    "source_evidence_reference": f"{root}/bundles/{bundle}",
                    "generation_seed": metadata["seed"],
                    "generation_config_sha256": metadata["config_sha256"],
                    "generator_sha256": metadata["generator_sha256"],
                    "generation_set": spec["set_id"],
                    "implementation_checkpoint": metadata["implementation_checkpoint"],
                    "dataset_freeze_commit": f"{root}/GENERATION.json",
                    "sealed_manifest_sha256": file_hash(ROOT / root / "MANIFEST.sha256"),
                }
            )

    cases: list[dict[str, object]] = [*development, *sealed]
    status_counts: dict[str, int] = {}
    for data in case_data.values():
        status = str(data["expected"]["status"])
        status_counts[status] = status_counts.get(status, 0) + 1

    other_files = sorted(CASES.glob("*.json"))
    manifest_entries = manifest_lines([*dev_files, *other_files, EVALUATION / "oracle.py"])
    (EVALUATION / "MANIFEST.sha256").write_text(
        "\n".join(manifest_entries) + "\n", encoding="utf-8", newline="\n"
    )

    dataset = {
        "dataset_id": "stage4_final_v1",
        "frozen_on": "2026-11-18",
        "bundle_count": len(cases),
        "development_bundle_count": len(development),
        "sealed_bundle_count": len(sealed),
        "expected_outcome_counts": status_counts,
        "implementation_checkpoint": "b63da40",
        "dataset_freeze_commit": freeze,
        "config": {
            "path": "configs/default.toml",
            "sha256": file_hash(ROOT / "configs" / "default.toml"),
        },
        "label_isolation": (
            "Sealed labels live outside every bundle; the reviewer never receives them. "
            "The development cases are cross-checked by the independent oracle and are not "
            "reviewer inputs."
        ),
        "sealed_manifests": {
            spec["root"]: file_hash(ROOT / str(spec["root"]) / "MANIFEST.sha256")
            for spec in SEALED_SETS
        },
        "evaluation_manifest_sha256": hashlib.sha256(
            (EVALUATION / "MANIFEST.sha256").read_bytes()
        ).hexdigest(),
        "cases": cases,
    }
    (EVALUATION / "DATASET.json").write_text(
        json.dumps(dataset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        f"froze {len(cases)} cases ({len(development)} development, {len(sealed)} sealed); "
        f"expected counts {status_counts}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
