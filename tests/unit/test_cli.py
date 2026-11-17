from __future__ import annotations

from typer.testing import CliRunner

from verityflow import __version__
from verityflow.cli import app


def test_version_command() -> None:
    result = CliRunner().invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_help_is_available() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "VerityFlow" in result.stdout
