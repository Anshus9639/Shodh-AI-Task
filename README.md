# VerityFlow

AI-assisted workflow compliance reviewer. It determines whether versioned business requirements, a structured specification, a workflow graph and a linked Python implementation agree, and reports evidence-backed findings as JSON.

Status: **P0 scaffold only.** Stage 1 implementation has not started.

- Architecture and plan: [docs/implementation_plan.md](docs/implementation_plan.md)
- Design decisions: [docs/decision_log.md](docs/decision_log.md)

## Toolchain

- Python 3.12 (pinned via `uv`, see `.python-version`)
- Dependencies pinned in `uv.lock`

## Commands

```
uv sync
uv run verityflow --help
uv run verityflow version
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```
