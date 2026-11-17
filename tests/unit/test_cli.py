from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from verityflow import __version__
from verityflow.cli import app

ROOT = Path(__file__).resolve().parent.parent.parent
DEV = ROOT / "fixtures" / "dev"
DICTIONARY = DEV / "B01_starter" / "dictionary.json"
REQUIREMENTS = DEV / "B01_starter" / "requirements.txt"


def _run(*args: str) -> tuple[int, dict]:
    result = CliRunner().invoke(app, list(args))
    payload = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
    return result.exit_code, payload


def test_version_command() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_help_is_available() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "VerityFlow" in result.stdout


def test_parse_emits_rules_without_a_coverage_gap() -> None:
    code, payload = _run("parse", str(REQUIREMENTS), "--dictionary", str(DICTIONARY))
    assert code == 0
    assert payload["coverage_gap"] is False
    assert [rule["rule_ref"] for rule in payload["rules"]] == ["R1", "R2", "R3", "R4"]


def test_parse_reports_a_coverage_gap_for_unsupported_text(tmp_path: Path) -> None:
    document = tmp_path / "requirements.txt"
    document.write_text(
        "R1. Missing category -> manual review.\nThe ticket must be routed quickly.\n",
        encoding="utf-8",
    )
    code, payload = _run("parse", str(document), "--dictionary", str(DICTIONARY))
    assert code == 1
    assert payload["coverage_gap"] is True
    gaps = [
        finding
        for finding in payload["findings"]
        if finding["severity"] in {"BLOCKER", "COVERAGE_GAP"}
    ]
    assert [finding["category"] for finding in gaps] == ["UNSUPPORTED_LANGUAGE"]


def test_parse_rejects_an_unreadable_dictionary(tmp_path: Path) -> None:
    code, _ = _run("parse", str(REQUIREMENTS), "--dictionary", str(tmp_path / "absent.json"))
    assert code == 2


def test_resolve_clean_bundle_is_ok() -> None:
    code, payload = _run("resolve", str(DEV / "B01_starter"))
    assert code == 0
    assert payload["requirements_ref"] == "REQ@1.0"
    assert payload["effective_requirements"]["confirmed_candidate_id"] == "confirmed"


def test_resolve_reports_an_unapproved_change_without_a_gap() -> None:
    code, payload = _run("resolve", str(DEV / "B05_unapproved_change"))
    assert code == 0
    decisions = payload["effective_requirements"]["decisions"]
    assert decisions[0]["state"] == "unapproved"


def test_resolve_reports_conflicting_approvals_as_a_gap() -> None:
    code, payload = _run("resolve", str(DEV / "B06_conflicting_approvals"))
    assert code == 1
    decisions = payload["effective_requirements"]["decisions"]
    assert decisions[0]["state"] == "conflict"
    assert payload["effective_requirements"]["applied"] == []
    assert payload["effective_requirements"]["confirmed_candidate_id"] == "confirmed"
    assert [
        candidate["candidate_id"] for candidate in payload["effective_requirements"]["candidates"]
    ] == [
        "confirmed",
        "variant:C1@1",
    ]


def test_resolve_rejects_an_unknown_requirements_ref() -> None:
    code, _ = _run("resolve", str(DEV / "B01_starter"), "--requirements", "SPEC@1.0")
    assert code == 1


def test_map_all_artifacts_is_ok() -> None:
    code, payload = _run("map", str(DEV / "B01_starter"))
    assert code == 0
    assert payload["mappings"]["spec"]["complete"] is True
    assert payload["mappings"]["workflow"]["complete"] is True


def test_map_single_artifact() -> None:
    code, payload = _run("map", str(DEV / "B01_starter"), "--artifact", "spec")
    assert code == 0
    assert set(payload["mappings"]) == {"spec"}


def test_map_rejects_an_unknown_artifact_name() -> None:
    code, _ = _run("map", str(DEV / "B01_starter"), "--artifact", "source")
    assert code == 2


def test_map_reports_a_malformed_config(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("[limits\nmax_cells = ", encoding="utf-8")
    code, _ = _run("map", str(DEV / "B01_starter"), "--config", str(config))
    assert code == 2


def test_map_reports_a_renamed_bundle_as_complete() -> None:
    code, payload = _run("map", str(DEV / "B01_starter"))
    assert code == 0
    elements = [match["element_id"] for match in payload["mappings"]["spec"]["matches"]]
    assert elements == ["S1", "S2", "S3", "SPEC:default"]
    assert all(match["method"] == "inferred" for match in payload["mappings"]["spec"]["matches"])


def test_map_discloses_code_without_a_semantic_descriptor(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    shutil.copytree(DEV / "B01_starter", bundle)
    source = "def route(ticket):\n    return 'normal_queue'\n"
    (bundle / "rules.py").write_text(source, encoding="utf-8")
    manifest = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
    manifest["review_target"]["code"] = ["RULES@1.0"]
    manifest["artifacts"].append(
        {
            "artifact_id": "RULES",
            "version": "1.0",
            "kind": "python",
            "path": "rules.py",
            "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        }
    )
    (bundle / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")

    code, payload = _run("map", str(bundle), "--artifact", "spec")
    assert code == 1
    deferred = [
        finding for finding in payload["findings"] if finding["category"] == "DEFERRED_DESCRIPTOR"
    ]
    assert [finding["severity"] for finding in deferred] == ["COVERAGE_GAP"]


@pytest.mark.parametrize("bundle_name", ["B03_approved_change", "B06_conflicting_approvals"])
def test_resolve_reads_the_bundle_config_default(bundle_name: str) -> None:
    code, payload = _run("resolve", str(DEV / bundle_name))
    assert payload["bundle"] == bundle_name
    assert code in {0, 1}
