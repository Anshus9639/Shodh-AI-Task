from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from verityflow.schemas import SCHEMA_MODELS, build_schemas

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMAS = ROOT / "schemas"
DEV = ROOT / "fixtures" / "dev"
HOLDOUT = ROOT / "holdout" / "bundles"

_ARTIFACT_SCHEMAS = {
    "bundle.json": "bundle_manifest",
    "dictionary.json": "data_dictionary",
    "spec.json": "spec",
    "workflow.json": "workflow",
    "changes.json": "change_requests",
}

_FIXTURES = [*sorted(DEV.iterdir()), *sorted(HOLDOUT.iterdir())]


def test_exported_schemas_are_current() -> None:
    for name, schema in build_schemas().items():
        path = SCHEMAS / f"{name}.schema.json"
        assert path.is_file(), f"missing {path}; run scripts/export_schemas.py"
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk == schema, f"{path} is stale; run scripts/export_schemas.py"


def test_every_contract_has_a_schema() -> None:
    assert set(build_schemas()) == set(SCHEMA_MODELS)
    assert len(build_schemas()) == 10


@pytest.mark.parametrize("bundle", _FIXTURES, ids=lambda path: path.name)
def test_fixture_artifacts_match_their_schemas(bundle: Path) -> None:
    for file_name, schema_name in _ARTIFACT_SCHEMAS.items():
        path = bundle / file_name
        if not path.is_file():
            continue
        schema = json.loads((SCHEMAS / f"{schema_name}.schema.json").read_text(encoding="utf-8"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(payload)


def test_invalid_artifacts_are_rejected_by_the_schema() -> None:
    spec_schema = json.loads((SCHEMAS / "spec.schema.json").read_text(encoding="utf-8"))
    with pytest.raises(ValidationError):
        Draft202012Validator(spec_schema).validate(
            {"spec_id": "S", "version": "1", "rules": [{"id": "R", "priority": "high"}]}
        )
    dictionary_schema = json.loads(
        (SCHEMAS / "data_dictionary.schema.json").read_text(encoding="utf-8")
    )
    with pytest.raises(ValidationError):
        Draft202012Validator(dictionary_schema).validate(
            {"fields": [{"name": "amount", "type": "money"}]}
        )


def test_policy_and_effective_requirements_outputs_validate(dictionary) -> None:
    from verityflow.approvals.resolver import resolve_effective_requirements
    from verityflow.config import Config
    from verityflow.ingest.artifacts import parse_artifacts
    from verityflow.ingest.loader import load_bundle
    from verityflow.requirements.compile import compile_document

    loaded = load_bundle(DEV / "B03_approved_change")
    typed = parse_artifacts(loaded)
    assert typed.dictionary is not None
    policy, _ = compile_document(typed.requirements[0].document)
    effective = resolve_effective_requirements(
        policy,
        manifest=loaded.manifest,
        changes=typed.changes,
        dictionary=typed.dictionary,
        config=Config(),
        requirements_ref="REQ@1.0",
        changes_ref="CHG@1.0",
    )
    policy_schema = json.loads((SCHEMAS / "policy.schema.json").read_text(encoding="utf-8"))
    effective_schema = json.loads(
        (SCHEMAS / "effective_requirements.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(policy_schema).validate(json.loads(policy.model_dump_json()))
    Draft202012Validator(policy_schema).validate(
        json.loads(effective.confirmed().model_dump_json())
    )
    Draft202012Validator(effective_schema).validate(json.loads(effective.model_dump_json()))
