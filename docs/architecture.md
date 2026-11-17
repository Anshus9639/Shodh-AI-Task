# Architecture

VerityFlow is a deterministic-first reviewer. The Stage 1 slice covers contracts, ingestion, canonical IR, requirement parsing/compilation, approval resolution and semantic cross-artifact correspondence. Verification (behavioral equivalence, counterexamples, status) is Stage 2 and is intentionally absent.

## Data flow

```
bundle directory
  bundle.json (manifest: ids, versions, sha256, review target, approvals)
        │
        ▼
ingest/loader.py ── size limits, path containment, UTF-8, sha256 recomputation
        │            findings: PROVENANCE_FAILURE / LIMIT_EXCEEDED / STRUCTURAL
        ▼
ingest/artifacts.py ── typed artifacts + parsed requirement documents
        │
        ├── requirements/parser.py ── line-oriented grammar, TextSpan anchors
        │        └── requirements/compile.py ── ordered first-match Policy, exceptions
        │
        ├── approvals/resolver.py ── hash-bound, role-authorized approval resolution
        │        └── EffectiveRequirements (confirmed base + diagnostic variants)
        │
        └── mapping/extract.py ── semantic descriptors for requirements/spec/workflow
                 └── mapping/matcher.py ── ID-free correspondence with validated hints
                          └── MappingResult + findings
```

## Modules

| Module | Responsibility |
| --- | --- |
| `models/` | Frozen Pydantic v2 contracts (`extra="forbid"`), discriminated unions, JSON schemas. External artifacts use `models/conditions.py`; internal policy/execution uses `ir/model.py`. |
| `ingest/` | Bundle manifest loading, byte cap, path-escape rejection, hash verification, `LineIndex` anchor checks, artifact typing. |
| `ir/model.py` | Internal canonical typed condition IR: `And`, `Or`, `NotNode`, `NotTrue`, `Cmp`, `Eq`, `Ne`, `In`, `NotIn`, `Missing`, `Const`. Numeric literals are exact `Fraction`s. |
| `models/conditions.py` | External condition contract accepted from source/spec artifacts. Mirrors the public grammar (`And`/`Or`/`Not` re-keyed as external nodes) and excludes the internal `NotTrue` guard. `to_ir` is the only boundary conversion. |
| `models/requirements.py` | Standalone contract for anchored plain-text requirements: artifact id/version/path/sha256/encoding, dictionary reference, and per-requirement `TextSpan` evidence. |
| `ir/evaluate.py` | Kleene three-valued evaluation over `CellValue` (ABSENT/NULL/PRESENT). A rule fires only on TRUE. |
| `ir/normalize.py` | Field/unit/destination canonicalization and JSON-pointer diagnostics. |
| `ir/units.py` | Exact unit families and canonicalization (seconds, cents). |
| `ir/domains.py` | Integer, fixed-scale decimal and continuous-rational domains; boundary/witness representatives. |
| `ir/policy.py` | `Rule`/`Policy`/`EffectiveRequirements` and the change-state vocabulary. |
| `requirements/` | Lexer, restricted English grammar, `RequirementDocument`, ordered first-match compiler. |
| `approvals/` | Effective-requirements resolution: approval binding, supersession, conflicts, candidate cap. |
| `mapping/` | Semantic descriptors, equivalence signatures, hint validation, ambiguity reporting. |
| `schemas.py` | Nineteen exported JSON Schemas (Draft 2020-12) built from the same Pydantic models, including standalone contracts for each input surface. |
| `cli.py` | `version`, `parse`, `resolve`, `map`; JSON on stdout; exit codes 0/1/2. |

## Invariants

1. **No ID-based equivalence.** Mapping never compares element ids. A supplied `trace_map` is a hint that must pass the same semantic equivalence test; invalid hints are reported and ignored.
2. **No arbitrary disambiguation.** When several semantically equivalent candidates remain, the mapping is `AMBIGUOUS_MAPPING` (`COVERAGE_GAP`); the matcher does not pick one.
3. **First-match exactness.** Compiled guards use the internal `NotTrue` node so that UNKNOWN earlier conditions do not block later entries and TRUE earlier conditions always do. `NotTrue` is internal-only: external spec/workflow/dictionary conditions are validated against `ExternalCondition`, which has no `not_true` variant.
4. **Authority is structured and hash-bound.** Only manifest approval records from authorized roles, bound to `change_id` + `change_version` + canonical payload SHA-256, carry authority. Conflicts leave scope undetermined and produce diagnostic-only variants.
5. **Exact arithmetic.** No floats in the IR; unit conversion is `Fraction`-based.
6. **Provenance is checked.** Artifact hashes are recomputed; evidence anchors are checked against loaded text; Python artifacts must declare `CodeLink` provenance.
7. **Honest incompleteness.** Unsupported language, invalid hints, ambiguous correspondence, stale/invalid approvals and limit overruns are findings; they are never silently dropped.

## Condition layers

Three condition layers are kept distinct:

1. **External artifact conditions** (`models/conditions.py`): what a specification, workflow branch or dictionary predicate may submit. The discriminated union is `cmp | eq | ne | in | not_in | missing | and | or | not | const`; `not_true` is rejected during artifact validation and by the exported schemas.
2. **Internal compiled-policy conditions** (`ir/model.py`): the dense ordered first-match policy produced by the requirement compiler and the approval resolver. Guards are `cond_i ∧ ⋀_{j<i} NotTrue(cond_j)`.
3. **Internal execution guards** (`NotTrue`): used by evaluation and mapping to encode "no earlier rule/branch fired". UNKNOWN satisfies `NotTrue`, which is what makes ordered first-match semantics exact.

## Contracts and schemas

`models/` holds the single source of truth. `python scripts/export_schemas.py` writes `schemas/*.schema.json`; `tests/unit/test_schemas.py` fails if the files on disk drift from the models and validates every dev and development-evidence artifact against them.

Standalone schemas cover the five required input surfaces:

| Input surface | Schemas |
| --- | --- |
| Anchored source requirements | `requirements_source`, `anchored_requirement` |
| Structured specification | `spec`, `spec_rule`, `spec_default` |
| Versioned workflow nodes and branches | `workflow`, `workflow_node`, `workflow_branch` |
| Linked Python code | `artifact_entry` (requires `link` when `kind="python"`), `code_link` |
| Approval records | `approval_record` |

`tests/unit/test_contracts.py` exercises artifact versioning, source-anchor resolution, approval metadata/hash binding and linked-code provenance. JSON Schema cannot express the kind-conditional `link` requirement on `artifact_entry`; the model validator enforces it during ingestion, while the exported `code_link` schema defines the link object itself.

## Configuration

`configs/default.toml` declares every limit explicitly (identical to the in-code defaults). Unknown keys, malformed TOML and missing files are rejected (`ConfigError`, CLI exit 2). Only roles in `[approvals].authorized_roles` can grant authority.

## Stage boundary

`src/verityflow/verify/` remains an empty package. No behavioral equivalence, counterexample generation, restricted-Python interpretation, model integration or status computation exists yet; those are Stage 2/3 and must not be inferred from mapping results. Mapping `complete` means "a unique semantic correspondence was established", not "the artifacts agree behaviorally".
