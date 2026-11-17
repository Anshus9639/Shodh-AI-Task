# VerityFlow

Deterministic-first workflow compliance reviewer. It parses versioned business requirements, compiles them into an ordered first-match policy, resolves hash-bound approvals, and verifies the specification, workflow graph and linked Python router against that policy over a disclosed ticket space. Linked Python is AST-interpreted, never executed. Every claim carries evidence; unsupported input, ambiguity and sampling are reported, never guessed.

Status: **Stage 3 complete (awaiting review).** The deterministic reviewer and `review` CLI are implemented and tagged `stage2-baseline` (`4702939`); the Stage 2 status correction is `f0dbb0c`. Stage 3 adds a supplementary model layer (`off`, `fake`, `gemini`) that can only add advisory collections and can never change a status, finding, approval decision or configuration. Stage 4 is not implemented.

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

# Deterministically review every linked artifact; full report as JSON.
uv run verityflow review fixtures/dev/B10_router_clean

# Optional supplementary model analysis, explicitly opt-in. Default is `off`.
uv run verityflow review fixtures/dev/B10_router_clean --model fake --model-scenario valid
uv run verityflow review fixtures/dev/B12_router_threshold --model gemini   # needs credentials
```

`review` exit codes: `0` PASS, `1` CHANGES_REQUIRED or INCOMPLETE, `2` invalid input (unreadable bundle, malformed config, bad option). The exit code always follows the deterministic report, in every model mode. With `--model off` (the default) the command prints the unchanged `ReviewReport`; enabled modes print `{"review": ..., "model_analysis": ...}`. The Stage 1 commands share exit codes `0`/`1`/`2` for no gap, gap/blocker, and invalid input.

Model modes:

- `off` — no model code is constructed. The reviewer is fully offline and deterministic.
- `fake` — synthetic, reproducible scenarios (including failures and prompt injection) for tests and demos. Never contacts a provider and never reports token counts.
- `gemini` — optional live adapter over the standard library, disabled unless selected. Credentials come only from `VERITYFLOW_GEMINI_API_KEY` (or `GEMINI_API_KEY`) and are never written to git or logs; a missing key fails closed before any network call.

Model output is advisory. It is validated against the deterministic report (known rule references, artifact ids/versions, exact anchors and verbatim excerpts) and split into **assisted** observations (consistent with deterministic coverage), **unverified** observations (quoted correctly but with no deterministic confirmation) and **rejected** entries. No Stage 3 path promotes a model observation to a finding or blocker, so a timeout, malformed response or injection attempt cannot alter the review.

## Contracts

The Pydantic models in `src/verityflow/models/` are the source of truth. Beyond the bundle, spec, workflow, dictionary, change and report contracts, standalone schemas are exported for the five required input surfaces: anchored requirements (`requirements_source`, `anchored_requirement`), specification (`spec`, `spec_rule`, `spec_default`), versioned workflow (`workflow`, `workflow_node`, `workflow_branch`), linked Python code (`artifact_entry`, `code_link`) and approvals (`approval_record`). Stage 3 adds `model_request`, `model_response` and `model_analysis` (22 schemas total). Regenerate the committed schemas with:

```
uv run python scripts/export_schemas.py   # writes schemas/*.schema.json
```

Fixtures are generated deterministically:

```
uv run python scripts/build_fixtures.py             # fixtures/dev + sealed Stage 1 holdout
uv run python scripts/build_stage2_holdout.py       # sealed Stage 2 semantic holdout
uv run python scripts/build_stage2_dev_fixtures.py  # fixtures/dev B10-B14 only
```

Both `holdout/` and `holdout_stage2/` are frozen after their freezing commits; do not regenerate them.

`fixtures/dev_evidence/holdout_v1/` is frozen historical evidence and is not regenerated.

## Quality gates

```
uv run pytest -q                 # complete default suite (unit + integration)
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Current results: 372 default tests passed; ruff, ruff format and strict mypy (`src`, 60 files) clean. Neither sealed set (`holdout/`, `holdout_stage2/`) has been executed; they are reserved for final Stage 1 and Stage 2 evaluation. No live model request was made: Stage 3 tests use the fake client and assert the Gemini adapter fails closed without credentials.
