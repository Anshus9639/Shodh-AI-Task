# VerityFlow

Deterministic-first workflow compliance reviewer. It parses versioned business requirements, compiles them into an ordered first-match policy, resolves hash-bound approvals, and infers ID-free semantic correspondence to a specification and workflow graph. Every claim carries evidence; unsupported input and ambiguity are reported, never guessed.

Status: **Stage 1 complete (awaiting review).** Verification (behavioral equivalence, counterexamples, restricted-Python analysis, status) and model integration are not implemented.

## Documentation

- [Architecture](docs/architecture.md)
- [Supported requirement language](docs/supported_language.md)
- [Evaluation protocol](docs/evaluation_protocol.md)
- [Limitations](docs/limitations.md)
- [Debugging log](docs/debugging_log.md)
- [Implementation plan](docs/implementation_plan.md)
- [Decision log](docs/decision_log.md)

## Toolchain

- Python 3.12 (pinned via `uv`, see `.python-version`)
- Pydantic v2 contracts; Typer CLI; JSON Schema export
- Dependencies pinned in `uv.lock`

## Setup

```
uv sync
uv run verityflow --help
```

## Commands

```
uv run verityflow version

# Parse a requirement document against a data dictionary (JSON on stdout).
uv run verityflow parse fixtures/dev/B01_starter/requirements.txt \
  --dictionary fixtures/dev/B01_starter/dictionary.json

# Resolve approvals into the effective requirements policy.
uv run verityflow resolve fixtures/dev/B03_approved_change

# Infer semantic correspondence to spec and workflow (or --artifact spec|workflow).
uv run verityflow map fixtures/dev/B01_starter --artifact all
```

Exit codes: `0` no coverage gap, `1` a blocker/coverage gap was found, `2` invalid input (unreadable file, malformed config, bad option).

## Contracts

The Pydantic models in `src/verityflow/models/` are the source of truth. Beyond the bundle, spec, workflow, dictionary, change and report contracts, standalone schemas are exported for the five required input surfaces: anchored requirements (`requirements_source`, `anchored_requirement`), specification (`spec`, `spec_rule`, `spec_default`), versioned workflow (`workflow`, `workflow_node`, `workflow_branch`), linked Python code (`artifact_entry`, `code_link`) and approvals (`approval_record`). Regenerate the committed schemas with:

```
uv run python scripts/export_schemas.py   # writes schemas/*.schema.json
```

Fixtures are generated deterministically:

```
uv run python scripts/build_fixtures.py   # fixtures/dev + sealed holdout + MANIFEST.sha256
```

`fixtures/dev_evidence/holdout_v1/` is frozen historical evidence and is not regenerated.

## Quality gates

```
uv run pytest -q                 # complete default suite (unit + integration)
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Current results: 215 default tests passed; ruff, ruff format and strict mypy clean. The sealed `holdout/` set has not been executed and is reserved for final evaluation.
