# Architecture

VerityFlow is a deterministic-first reviewer. Stage 1 covers contracts, ingestion, canonical IR, requirement parsing/compilation, approval resolution and semantic cross-artifact correspondence. Stage 2 adds behavioural verification: the effective requirement policy, the specification, the workflow graph and the linked Python router are compiled into one routing contract and compared over a single disclosed ticket space, producing a status, validated counterexamples, coverage evidence and unreviewed scopes. Stage 3 adds an optional, advisory model layer that reads the deterministic report and can only contribute validated observations; the deterministic engine remains the sole authority.

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

Stage 2 (verify/):
  confirmed effective Policy ─┬─ spec.json   → build_spec_router
                              ├─ workflow.json → build_workflow_router
                              └─ router.py   → compile_router (AST parse + interpret)
                                        │
                                        ▼
                        build_ticket_space (domain partitions, extra code boundaries)
                                        │
              ┌─────────────────────────┼───────────────────────────┐
              ▼                         ▼                           ▼
        verify_spec              verify_workflow               verify_code
              └─────────────────────────┼───────────────────────────┘
                                        ▼
              analyze_interactions → analyze_coverage → ReviewReport
                                        │
                                        ▼  (optional, default off)
Stage 3 (model/):
  ReviewReport + LoadedBundle ── build_model_request (bounded; evidence catalogue)
        │                              └── render_prompt (versioned, delimited payload)
        ▼
  ModelClient (fake | gemini) ── timeout/retry ── raw text
        │
        ▼
  validate_response (size, JSON, schema, refs, anchors, excerpts)
        │
        ▼
  classify observations → assisted / unverified / rejected
        │
        ▼
  ModelAnalysis (status copied verbatim; never merged into ReviewReport)
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
| `verify/routing.py` | Shared `Router` contract, `PolicyRouter`, spec router, routing comparison, counterexample construction and validation. |
| `verify/domain.py` | Ticket-space enumeration from declared field domains; extra cuts/literals from linked code; exhaustive vs pairwise-sampled disclosure. |
| `verify/spec.py` | Behavioural comparison of the effective policy with the specification. |
| `verify/workflow.py` | Ordered branch-list workflow interpreter and behavioural comparison. |
| `verify/python_ast.py` | AST-only restricted-Python interpreter for the linked router; no `eval`/`exec`/import; budgets and an explicit outcome vocabulary. |
| `verify/code.py` | Router comparison, diagnosis (precedence, numeric boundary, exception), runtime/unsupported separation, code-vs-workflow agreement. |
| `verify/equivalence.py` | Firing-equivalence for first-match guards, perfect matching/reordering detection, numeric boundary classification. |
| `verify/interactions.py` | Shadowed requirement rules and exceptions that fire outside their target. |
| `verify/coverage.py` | Per-requirement witnesses and evidence mapping to spec/workflow/code; unreviewed coverage scopes. |
| `verify/review.py` | End-to-end orchestration and report/status truth table. |
| `verify/models.py` | `ReviewReport`, `ComparisonRecord`, `Counterexample`, `RequirementCoverage`, `ReviewStatus`. |
| `model/models.py` | Frozen Stage 3 contracts: `ModelRequest`, `ModelResponse`, observations, failures, provenance, `ModelAnalysis`. |
| `model/prompt.py` | Versioned prompt rendering (`prompts/reviewer_v1.txt`); escapes the JSON payload so artifact text cannot close the untrusted delimiter. |
| `model/request.py` | Bounded request construction from a loaded bundle and the deterministic report, including an exact (anchor, excerpt) evidence catalogue. |
| `model/validate.py` | Response size, JSON, schema, reference, anchor, excerpt, category and duplicate validation. |
| `model/service.py` | Provider-neutral orchestration: retries, provenance, classification, three collections and an optional in-memory cache. |
| `model/fake.py` | Reproducible synthetic scenarios, labelled synthetic, with no fabricated token counts. |
| `model/gemini.py` | Optional stdlib `urllib.request` adapter; env-configured, disabled unless `--model gemini`. |
| `schemas.py` | Twenty-two exported JSON Schemas (Draft 2020-12) built from the same Pydantic models, including standalone contracts for each input surface plus `model_request`, `model_response` and `model_analysis`. |
| `cli.py` | `version`, `parse`, `resolve`, `map`, `review`; `review --model off|fake|gemini`; JSON on stdout; exit codes 0/1/2. |

## Invariants

1. **No ID-based equivalence.** Mapping never compares element ids. A supplied `trace_map` is a hint that must pass the same semantic equivalence test; invalid hints are reported and ignored.
2. **No arbitrary disambiguation.** When several semantically equivalent candidates remain, the mapping is `AMBIGUOUS_MAPPING` (`COVERAGE_GAP`); the matcher does not pick one.
3. **First-match exactness.** Compiled guards use the internal `NotTrue` node so that UNKNOWN earlier conditions do not block later entries and TRUE earlier conditions always do. `NotTrue` is internal-only: external spec/workflow/dictionary conditions are validated against `ExternalCondition`, which has no `not_true` variant.
4. **Authority is structured and hash-bound.** Only manifest approval records from authorized roles, bound to `change_id` + `change_version` + canonical payload SHA-256, carry authority. Conflicts leave scope undetermined and produce diagnostic-only variants.
5. **Exact arithmetic.** No floats in the IR; unit conversion is `Fraction`-based.
6. **Provenance is checked.** Artifact hashes are recomputed; evidence anchors are checked against loaded text; Python artifacts must declare `CodeLink` provenance.
7. **Honest incompleteness.** Unsupported language, invalid hints, ambiguous correspondence, stale/invalid approvals, unsupported code, runtime errors and limit overruns are findings; they are never silently dropped.
8. **Bundle Python is data, not code under execution.** The router is parsed with `ast` and evaluated by a closed interpreter over literals and ticket lookups. Imports (other than `typing`/`__future__`), calls, attributes, loops and arbitrary statements are rejected as `UNSUPPORTED`; the process never executes artifact code.
9. **Behaviour is the only evidence.** Two routes correspond only when every compared interpreter returns the same outcome on witnesses that were re-evaluated after being recorded. Rule IDs, node names and destination-name similarity never establish correctness.
10. **Sampling is never called exhaustive.** A result is `exhaustive` only when the full representative cross-product was evaluated, a policy was confirmed, and every comparison ran in exhaustive mode. Otherwise skipped combinations are counted and disclosed, and the review cannot be PASS.
11. **Model output is advisory and structurally separate.** A `ModelAnalysis` is emitted beside the `ReviewReport`, never merged into it. The report's status, findings, approvals and effective policy are copied, not recomputed, and no code path promotes a model observation to a finding. A model claim about status is ignored.
12. **Evidence is validated, not trusted.** Every cited rule reference, artifact id/version and anchor must exist in the request catalogue, and every quotation must match the supplied excerpt exactly. A valid quotation proves provenance only, not interpretation; observations that cannot be tied to deterministic coverage remain `unverified`.
13. **Artifact content is untrusted data.** The prompt wraps the full request payload in a delimited block after escaping angle brackets, and the system instruction treats it as data. Instructions, approval claims or status demands inside requirements, code comments, workflow labels, spec text or metadata cannot alter configuration or authority; such claims are reported as observations with an `UNTRUSTED_APPROVAL_CLAIM` category.
14. **Model failure is disclosed and optional.** Timeout, rate limit, authentication, malformed or truncated JSON, schema failure, invented references, excessive output and retry exhaustion each have an explicit category and are recorded as a failed or skipped analysis. The deterministic report is unchanged and still returned; the CLI exit code follows the deterministic status.

## Model layer (Stage 3)

The model layer is an optional side-channel over a completed deterministic review.

- **Authority table.** Every combination leaves `ModelAnalysis.deterministic_status` equal to `ReviewReport.status` and sets `status_unchanged = True`. PASS + a model blocker claim stays PASS; CHANGES_REQUIRED + a PASS claim stays CHANGES_REQUIRED; INCOMPLETE + a PASS claim stays INCOMPLETE; CHANGES_REQUIRED or INCOMPLETE + timeout or malformed output keeps the deterministic status; PASS + an optional model failure stays PASS.
- **Three collections.** `confirmed_findings` is a copy of the deterministic report's blockers. `assisted_observations` are validated observations whose rule reference is deterministically covered and whose quotation matches supplied evidence; they carry an explicit consistency statement, not a new severity. `unverified_observations` are well-formed but cannot be tied to deterministic coverage (candidate mappings, ambiguities, suggested tests, uncovered rules) and require human review. `rejected` records every invalid observation and why. Only the first two are ever read by a human; none are merged into `ReviewReport.findings`.
- **Request bounding.** `ModelRequest` carries bundle identity, prompt version, model configuration, rule/finding/coverage/unreviewed summaries and an evidence catalogue. Any single excerpt over 2000 characters or a request over `max_request_bytes` produces a skipped analysis (`REQUEST_TOO_LARGE`); nothing is truncated, no mandatory requirement is dropped, and the deterministic report remains available.
- **Validation.** Responses over `max_response_bytes` are rejected without retry (`EXCESSIVE_RESPONSE`). JSON that fails to parse is distinguished from plausibly truncated JSON; schema violations (including extra fields and duplicate ids) and invented references, versions, anchors or altered quotations are rejected per observation. A rejected observation never enters the assisted or unverified sets and never affects status.
- **Failure handling.** Timeout, connection failure, rate limit and server error are retried up to `max_retries`; exhaustion becomes `RETRY_EXHAUSTED` with the underlying cause. Authentication, schema, reference and size failures are not retried. Each call records provider/model identity, provider request id, model version, finish reason, usage metadata, latency, retry count and a success/failure category. No prompt text, artifact text or credential is logged.
- **Cache.** When enabled, completed analyses are cached in memory under bundle digest, artifact versions, approval hashes, effective-policy digest, report digest, prompt version, provider/model and generation settings. Any change invalidates the key, and a cached entry is reused only when its `deterministic_status` matches the current report; stale PASS output is never served.
- **Fake vs live.** `FakeModelClient` replays fixed synthetic scenarios (valid, candidate mapping, ambiguity, fabricated references, wrong version, malformed, truncated, empty, conflicting, blocker claim, injection, timeout, rate limit, excessive output, retry exhaustion). Its metadata is marked synthetic and carries no token counts. The Gemini adapter is exercised only when explicitly selected; ordinary tests never contact a provider, and the adapter fails closed without credentials.

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

`configs/default.toml` declares every deterministic limit explicitly (identical to the in-code defaults). Unknown keys, malformed TOML and missing files are rejected (`ConfigError`, CLI exit 2). Only roles in `[approvals].authorized_roles` can grant authority.

Model settings live in code (`config.ModelSettings`: `mode="off"`, model name, prompt version, timeout, `max_retries`, generation limits, request/response caps, observation cap, fake scenario, cache toggle). The committed `configs/default.toml` deliberately has no `[model]` section because its SHA-256 is recorded in the sealed Stage 2 generation metadata; adding one would change a frozen provenance hash. A user-supplied config may still include a `[model]` section, and `--model`/`--model-scenario` override the mode for one invocation. Gemini credentials come only from the environment and are never part of any model, request or log record.

## Stage boundary

Stage 1 covers parsing, compilation, approval resolution and semantic mapping. Stage 2 is complete through the deterministic reviewer (`verify/review.py`, `verityflow review`), which decides PASS / CHANGES_REQUIRED / INCOMPLETE from evaluated behaviour only. Stage 3 is complete through the optional model layer (`verityflow review --model ...`): it may suggest candidate interpretations, ambiguity notes, finding explanations, interactions, extra ticket tests and human-review evidence, but every output is advisory and validated. There is no model-to-blocker bridge: a model claim cannot create a finding, change the effective policy or approval, or alter the status. Mapping `complete` still means "a unique semantic correspondence was established"; behavioural agreement is a separate Stage 2 result. Stage 4 does not exist.

## Status truth table

1. Any reproducible, independently validated `BLOCKER` finding → `CHANGES_REQUIRED`, even when the ticket space was sampled and other combinations remain unreviewed. Every validated blocker stays in the report, and all skipped or incomplete scope is still disclosed.
2. Otherwise, any incomplete mandatory check — a `COVERAGE_GAP` finding, a sampled space, an unreviewed scope or an unconfirmed effective policy → `INCOMPLETE`.
3. Otherwise, when every effective requirement is covered, every mandatory comparison completed, the supported domain was exhaustively evaluated and no blocker was established → `PASS`.

A sampled review can therefore establish `CHANGES_REQUIRED`: absence from a sample never creates a blocker, but a counterexample that was evaluated inside the sample and independently re-routed is real evidence of a defect, and unreviewed combinations elsewhere cannot downgrade it. Sampling only caps the status at `INCOMPLETE` when no validated blocker exists. `exhaustive = false` and the sampling/unreviewed disclosures remain in the report either way.

`exhaustive` requires the space to be exhaustive, the effective policy to be confirmed, and every comparison record to report exhaustive mode.
