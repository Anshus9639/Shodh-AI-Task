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
| `models/` | Frozen Pydantic v2 contracts (`extra="forbid"`), discriminated unions, JSON schemas. |
| `ingest/` | Bundle manifest loading, byte cap, path-escape rejection, hash verification, `LineIndex` anchor checks, artifact typing. |
| `ir/model.py` | Canonical typed condition IR: `And`, `Or`, `NotNode`, `NotTrue`, `Cmp`, `Eq`, `Ne`, `In`, `NotIn`, `Missing`, `Const`. Numeric literals are exact `Fraction`s. |
| `ir/evaluate.py` | Kleene three-valued evaluation over `CellValue` (ABSENT/NULL/PRESENT). A rule fires only on TRUE. |
| `ir/normalize.py` | Field/unit/destination canonicalization and JSON-pointer diagnostics. |
| `ir/units.py` | Exact unit families and canonicalization (seconds, cents). |
| `ir/domains.py` | Integer, fixed-scale decimal and continuous-rational domains; boundary/witness representatives. |
| `ir/policy.py` | `Rule`/`Policy`/`EffectiveRequirements` and the change-state vocabulary. |
| `requirements/` | Lexer, restricted English grammar, `RequirementDocument`, ordered first-match compiler. |
| `approvals/` | Effective-requirements resolution: approval binding, supersession, conflicts, candidate cap. |
| `mapping/` | Semantic descriptors, equivalence signatures, hint validation, ambiguity reporting. |
| `schemas.py` | Ten exported JSON Schemas (Draft 2020-12) built from the same Pydantic models. |
| `cli.py` | `version`, `parse`, `resolve`, `map`; JSON on stdout; exit codes 0/1/2. |

## Invariants

1. **No ID-based equivalence.** Mapping never compares element ids. A supplied `trace_map` is a hint that must pass the same semantic equivalence test; invalid hints are reported and ignored.
2. **No arbitrary disambiguation.** When several semantically equivalent candidates remain, the mapping is `AMBIGUOUS_MAPPING` (`COVERAGE_GAP`); the matcher does not pick one.
3. **First-match exactness.** Compiled guards use the internal `NotTrue` node so that UNKNOWN earlier conditions do not block later entries and TRUE earlier conditions always do.
4. **Authority is structured and hash-bound.** Only manifest approval records from authorized roles, bound to `change_id` + `change_version` + canonical payload SHA-256, carry authority. Conflicts leave scope undetermined and produce diagnostic-only variants.
5. **Exact arithmetic.** No floats in the IR; unit conversion is `Fraction`-based.
6. **Provenance is checked.** Artifact hashes are recomputed; evidence anchors are checked against loaded text.
7. **Honest incompleteness.** Unsupported language, invalid hints, ambiguous correspondence, stale/invalid approvals and limit overruns are findings; they are never silently dropped.

## Contracts and schemas

`models/` holds the single source of truth. `python scripts/export_schemas.py` writes `schemas/*.schema.json`; `tests/unit/test_schemas.py` fails if the files on disk drift from the models and validates every dev and holdout artifact against them.

## Configuration

`configs/default.toml` declares every limit explicitly (identical to the in-code defaults). Unknown keys, malformed TOML and missing files are rejected (`ConfigError`, CLI exit 2). Only roles in `[approvals].authorized_roles` can grant authority.

## Stage boundary

`src/verityflow/verify/` remains an empty package. No behavioral equivalence, counterexample generation, restricted-Python interpretation, model integration or status computation exists yet; those are Stage 2/3 and must not be inferred from mapping results. Mapping `complete` means "a unique semantic correspondence was established", not "the artifacts agree behaviorally".
