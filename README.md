# VerityFlow

VerityFlow is a deterministic-first workflow compliance reviewer. It reads a versioned bundle of
business requirements, resolves hash-bound approvals into an effective policy, and verifies a
structured specification, a webhook workflow and an optional linked Python router against that
policy over a disclosed ticket space. Linked Python is interpreted from its AST, never executed.
Every status is backed by evaluated behaviour, and every finding carries references that resolve
against the artifact bytes. Unsupported input, ambiguity and sampling are reported, never guessed.

## Main capabilities

- **Anchored requirements.** A restricted requirement grammar compiled from source text, with each
  rule bound to its exact line/column span.
- **Approval resolution.** Approvals bind to the exact change payload by SHA-256; only authorized
  roles and valid supersession chains grant authority. Disputed changes are isolated, not applied.
- **Ordered first-match policy.** Guard *i* is `cond_i ∧ ⋀_{j<i} NotTrue(cond_j)` over declared
  domains, evaluated with exact `Fraction` arithmetic and three-valued Kleene logic.
- **Semantic artifact verification.** Spec and workflow correspondence survives renamed identifiers;
  workflow branches become ordered path conditions with unreachable/cyclic/fall-through diagnosis.
- **Restricted code interpretation.** One `route(ticket)` entry point, AST-interpreted with
  node/step/depth limits; code/workflow comparison is separate from requirements comparison.
- **Evidence-backed findings.** Every confirmed finding carries requirement-side and
  implementation-side `evidence_refs` (artifact, version, anchor, path, hash, excerpt, role) and a
  non-empty correction; unresolvable evidence downgrades to a disclosed coverage gap.
- **Conflict-aware verification.** Each witness is re-evaluated under the confirmed policy and each
  permitted diagnostic candidate; conflict-dependent differences become `UNDETERMINED_CONFLICT` /
  `COVERAGE_GAP` instead of blockers.
- **Optional advisory model layer.** `off` (default), `fake` (synthetic replay) or `gemini`
  (opt-in, fails closed without credentials); model output can never change a status or finding.
- **Frozen evaluation.** Independent oracle, baseline adapter, controlled ablation, schema-validated
  reports, and a sealed set executed once and never used for tuning.

## Architecture overview

```
bundle.json ──► ingest (schemas, byte limits, SHA-256)
                    │
        ┌───────────┼────────────┬───────────────┐
        ▼           ▼            ▼               ▼
  requirements   approvals    spec           workflow / linked Python
  (grammar)      (hash-bound) (semantic)     (graph / restricted AST)
        │           │            │               │
        └────► effective policy ─┴──► behaviour comparison per ticket
                                             │
                            coverage · interactions · counterexamples
                                             │
                            conflict analysis · evidence resolution
                                             │
                                  status + JSON report (schema-valid)
```

Status truth table: a validated blocker → `CHANGES_REQUIRED`; otherwise any incomplete mandatory
scope → `INCOMPLETE`; otherwise `PASS`. `PASS` additionally requires an exhaustive ticket space and
exhaustive comparison modes.

## Technology stack

- Python 3.12 (pinned in `.python-version`), packaged with Hatchling (`src/` layout)
- Pydantic v2 frozen contracts (`extra="forbid"`) exported to JSON Schema
- Typer CLI, pytest + jsonschema, Ruff, strict mypy
- `uv` for environment and lockfile management (`uv.lock`)
- Standard library only for the optional Gemini adapter; no runtime service dependency

## Repository structure

```
src/verityflow/       reviewer: ingest, requirements, approvals, mapping, verify, evidence, model, cli
tests/unit/           unit suite (contracts, parser, domains, evidence, model, schemas, isolation)
tests/integration/    end-to-end review, remediation approval/conflict/semantic suites
fixtures/dev/         development bundles B01–B14
holdout/              sealed Stage 1 holdout F01–F05 (frozen, not executed by the offline run)
holdout_stage2/       sealed Stage 2 semantic holdout G01–G05 (frozen)
evaluation/           frozen dataset, independent oracle, harness, baseline, remediation bundles
configs/              effective configuration and the domain-partitioning ablation
schemas/              exported JSON Schemas for every input surface and report
scripts/              fixture builders, schema export, offline and sealed runners
reports/              generated reports: Stage 4 original, baseline, ablation, debugging,
                      remediation development, submission verification
docs/                 architecture, supported language, evaluation protocol, limitations,
                      debugging log, engineering note, AI assistance note
```

## Installation

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/); no API keys and no network access are
needed for the reviewer, the test suite or the offline evaluation.

```
uv sync
uv run verityflow --help
```

## Offline CLI usage

```
uv run verityflow version
uv run verityflow parse fixtures/dev/B01_starter/requirements.txt \
  --dictionary fixtures/dev/B01_starter/dictionary.json
uv run verityflow resolve fixtures/dev/B03_approved_change
uv run verityflow map fixtures/dev/B01_starter --artifact all
uv run verityflow review fixtures/dev/B01_starter
uv run verityflow review fixtures/dev/B11_router_precedence
uv run python scripts/run_offline.py          # tests, schemas, manifests, evaluation, baseline, ablation
```

`review` exit codes: `0` PASS, `1` CHANGES_REQUIRED or INCOMPLETE, `2` invalid input. The exit code
always follows the deterministic report. With `--model off` (default) the command prints the
`ReviewReport`; enabled modes print `{"review": ..., "model_analysis": ...}`. The `gemini` mode is
never exercised by the offline run and requires `GEMINI_API_KEY` or `VERITYFLOW_GEMINI_API_KEY`.

### Example input

`fixtures/dev/B01_starter/requirements.txt`:

```
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, waiting >= 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
```

The bundle `bundle.json` lists the requirement, spec, workflow and dictionary artifacts with their
paths and SHA-256 hashes; `dictionary.json` declares field domains and units.

### Example JSON output (abridged)

`uv run verityflow review fixtures/dev/B01_starter` prints a report with status `PASS`:

```json
{
  "bundle_id": "B01_starter",
  "bundle_version": "1.0",
  "status": "PASS",
  "requirements_ref": "REQ@1.0",
  "confirmed_candidate_id": "confirmed",
  "applied_changes": [],
  "checks": [
    {"name": "requirements", "required": true, "outcome": "pass"},
    {"name": "spec", "required": true, "outcome": "pass"},
    {"name": "workflow", "required": true, "outcome": "pass"},
    {"name": "code", "required": false, "outcome": "skipped",
     "detail": "no python router is linked in the review target"},
    {"name": "sampling", "required": true, "outcome": "pass",
     "detail": "all 360 representative ticket combinations were evaluated"}
  ],
  "comparisons": [
    {"name": "spec", "artifact_ref": "SPEC@1.0", "mode": "exhaustive", "equal": true,
     "cells_checked": 360, "combinations_total": 360, "combinations_skipped": 0,
     "counterexamples": [], "categories": []}
  ],
  "coverage": [
    {"rule_ref": "R1", "destination": "manual_review", "covered": true,
     "spec_elements": ["S1"], "workflow_elements": ["manual"],
     "evidence": ["requirement 'R1' fires at ...", "spec SPEC@1.0 routes ... via S1"]}
  ],
  "findings": [],
  "unreviewed": [],
  "conflict_analysis": [],
  "exhaustive": true
}
```

A defective bundle (`B11_router_precedence`) yields `CHANGES_REQUIRED` with a blocker whose
`evidence_refs` point at `requirements.txt:1-1` and `router.py:7-8`, each with the artifact hash and
a verbatim excerpt:

```json
{
  "finding_id": "F-PRECEDENCE-087a5100",
  "severity": "BLOCKER",
  "category": "PRECEDENCE",
  "source": "deterministic",
  "rule_ref": "R1",
  "artifact_id": "ROUTER",
  "artifact_version": "1.0",
  "location": {"kind": "text", "path": "requirements.txt", "line_start": 1, "line_end": 1,
               "excerpt": "R1. Missing category -> manual review."},
  "evidence_refs": [
    {"artifact_id": "REQ", "artifact_version": "1.0", "anchor_type": "source_line",
     "anchor_value": "requirements.txt:1-1", "evidence_role": "SOURCE_REQUIREMENT",
     "source_hash": "968c415f...", "excerpt": "R1. Missing category -> manual review."},
    {"artifact_id": "ROUTER", "artifact_version": "1.0", "anchor_type": "named_block",
     "anchor_value": "router.py:7-8", "evidence_role": "PYTHON_CODE",
     "source_hash": "10e0bd7b...", "excerpt": "if \"category\" not in ticket:\n        return \"manual\""}
  ]
}
```

## Report interpretation

- `status` is derived only from evaluated behaviour: `PASS`, `CHANGES_REQUIRED` (a validated
  blocker) or `INCOMPLETE` (mandatory scope not fully verified).
- `checks` are the mandatory/optional obligation list; a failed or skipped *required* check makes
  the result `INCOMPLETE`, not `PASS`.
- `comparisons` state the comparison mode (`exhaustive`, `sampled`), how many of the declared
  combinations were checked, and the retained `counterexamples`.
- `coverage` ties each requirement rule to the spec/workflow/code elements that implement it and the
  ticket that exercises it.
- `unreviewed` and `coverage_gaps` disclose what could not be verified; they are never silent.
- `findings` are confirmed defects; `evidence_refs` let a reader reproduce each claim from bytes.
- `conflict_analysis` lists approval interpretations that would change a witness and the resulting
  `UNDETERMINED_CONFLICT` / `COVERAGE_GAP` classification.
- `exhaustive` is true only when the whole declared ticket space was compared.

## Supported scope

Anchored requirements in the documented grammar; unique spec priorities; acyclic workflow branches;
one `route(ticket)` Python entry point restricted to comparisons, boolean logic, literals and
terminal returns (plus optional `typing`/`__future__` imports). Missing values are UNKNOWN and
`NotTrue(UNKNOWN)` is true, so first-match semantics stay exact over declared domains. Anything
outside this language is rejected or reported as `UNSUPPORTED`, never approximated.

## Limitations

- Real live-model quality, latency and cost are **not** measured; only fake-client contract scenarios
  are exercised, and the model layer is advisory by design.
- Arbitrary Python, imports other than those listed, and natural language outside the grammar are
  unsupported.
- Continuous or unbounded domains may be sampled rather than exhaustive; the report discloses this
  and the decision cannot be `PASS` on an incomplete space.
- Evidence excerpts for JSON artifacts are canonical projections, not byte-exact formatting.
- The datasets are small and authored in this repository, not an external benchmark. The sealed
  result is 8/10 and was not re-measured after remediation.
- The remediation label audit is an AI re-derivation by the implementation author, not human review.

## Evaluation methodology

- **Independent oracle.** Labels are produced by `evaluation/oracle.py`, a separately written
  first-match router that imports no reviewer code; sealed labels live outside bundles and never
  enter reviewer input. Metrics are computed from reviewer subprocess reports, and expected labels
  are applied only after execution.
- **Original Stage 4 (frozen).** 37 bundles: 27 development and 10 sealed. Reported results remain
  27/27 development decisions and 8/10 sealed decisions, 0 false PASS; the two sealed differences
  (F03, G03) are retained and explained by post-evaluation audit, not tuned away.
- **Remediation (separate).** 22 new development bundles (14 approval/revision/conflict, 8 semantic)
  evaluate 22/22 decisions with 16 confirmed findings, 16/16 structured evidence, 108/108 valid
  counterexamples and 4/4 conflict handling. These are development results on new synthetic bundles,
  not sealed performance.
- **Baseline.** `stage2-baseline` (`4702939`) runs from a `git archive` checkout on identical bytes
  and config; it also decides 27/27. **Ablation.** Disabling domain partitioning drops exact
  decisions to 10/27 with 0 false PASS, because the disabled reviewer abstains.
- **Debugging evidence.** Two real incidents are reproduced from history: each regression test fails
  at the parent commit and passes at the fix and at `HEAD`.
- **Sealed isolation.** The sealed F01–F05 and G01–G05 bundles were executed once by
  `scripts/run_final_evaluation.py`, are excluded from the offline run, and were not used for tuning.

## Test commands and quality gates

```
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python scripts/run_offline.py
```

Current results at this submission's source revision: **411 tests passed**; Ruff and Ruff format
clean; strict mypy clean over 66 source files; all 32 exported schemas match their models; the
offline runner exits 0 with schema-valid reports, unchanged sealed manifests and no credentials or
network; 0 false PASS and 0 false blocks on the development set. See
[docs/engineering_note.md](docs/engineering_note.md) for the measured evaluation detail and
[docs/submission_readiness.md](docs/submission_readiness.md) for the final audit.

## Documentation

- [Architecture](docs/architecture.md)
- [Supported requirement language](docs/supported_language.md)
- [Evaluation protocol](docs/evaluation_protocol.md)
- [Limitations](docs/limitations.md)
- [Debugging log](docs/debugging_log.md)
- [Implementation plan](docs/implementation_plan.md)
- [Decision log](docs/decision_log.md)
- [Remediation audit](docs/remediation_audit.md)
- [Engineering note](docs/engineering_note.md)
- [AI assistance note](docs/ai_assistance_note.md)
- [Submission readiness](docs/submission_readiness.md)

## Fixtures and frozen data

Fixtures are generated deterministically; the Stage 4 development dataset and its labels are frozen,
and regenerating them changes the hashes in `evaluation/MANIFEST.sha256` and `evaluation/DATASET.json`:

```
uv run python scripts/build_fixtures.py
uv run python scripts/build_stage2_holdout.py
uv run python scripts/build_stage2_dev_fixtures.py
uv run python scripts/build_stage4_eval_fixtures.py
uv run python scripts/build_eval_labels.py
uv run python scripts/build_dataset_manifest.py --freeze-commit <sha1>
```

Do not regenerate `holdout/`, `holdout_stage2/` or `fixtures/dev_evidence/holdout_v1/`; do not
regenerate the Stage 4 dataset to improve a result. `scripts/export_schemas.py` rewrites
`schemas/*.schema.json`.
