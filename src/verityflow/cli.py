from __future__ import annotations

import typer

from verityflow import __version__

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """VerityFlow - AI-assisted workflow compliance reviewer."""


@app.command()
def version() -> None:
    """Print the tool version."""
    typer.echo(__version__)
