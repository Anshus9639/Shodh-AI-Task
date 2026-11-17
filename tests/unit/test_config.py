from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from verityflow.config import Config, ConfigError, Limits, load_config

ROOT = Path(__file__).resolve().parent.parent.parent


def test_defaults() -> None:
    config = Config()
    assert config.limits.max_cells == 50_000
    assert config.limits.max_candidate_policies == 8
    assert config.approvals.authorized_roles == ("policy_owner",)


def test_config_is_frozen() -> None:
    config = Config()
    with pytest.raises(ValidationError):
        config.limits.max_cells = 1


def test_limits_reject_non_positive_values() -> None:
    with pytest.raises(ValidationError):
        Limits(max_cells=0)


def test_load_config_without_a_path_returns_defaults() -> None:
    assert load_config() == Config()


def test_default_toml_matches_the_code_defaults() -> None:
    assert load_config(ROOT / "configs" / "default.toml") == Config()


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[limits]\nmax_cells = 10\napprove_everything = true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="does not match the schema"):
        load_config(path)


def test_malformed_toml_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[limits\nmax_cells = ", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(path)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read configuration"):
        load_config(tmp_path / "absent.toml")


def test_overrides_are_applied(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[limits]\nmax_cells = 7\n\n[approvals]\nauthorized_roles = ["compliance"]\n',
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.limits.max_cells == 7
    assert config.approvals.authorized_roles == ("compliance",)
