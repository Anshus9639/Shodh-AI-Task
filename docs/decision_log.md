# Decision Log

Format: ID · date · decision · rationale · alternatives considered · status. Entries are append-only; superseded decisions are marked, not deleted.

---

### D-001 · 2026-11-17 · Deterministic-first; model is advisory-only
- **Decision:** All verdict-bearing checks are deterministic and offline. Model output can never create, remove or downgrade a blocker or coverage gap, and never affects status (unless `model.required=true`, in which case only the model's *failure* matters → INCOMPLETE).
- **Rationale:** The assignment forbids LLM approval authority and requires behavioral equivalence, which an LLM can't establish.
- **Alternatives:** LLM votes or tie-breaks — rejected because they are unverifiable.
- **Status:** Accepted.

### D-002 · 2026-11-17 · Verdict is behavioral; trace links are explanatory
- **Decision:** A defect exists iff a concrete in-domain ticket yields different outcomes. `implements` links only localize evidence and classify the defect.
- **Rationale:** Makes ID renames and reordered records verdict-invariant; avoids "rule-ID match" reviewers.
- **Status:** **Amended by D-018.** Behavioral equivalence remains the only source of counterexamples, but defects are also established by source coverage (missing requirements) and required evidence checks.

### D-003 · 2026-11-17 · Kleene three-valued logic for missing values
- **Decision:** Atomic predicates (except `Missing`) over an absent/null field are UNKNOWN; a rule or branch fires only when TRUE. ABSENT and NULL are separate partition states for code evaluation; both satisfy `is missing`.
- **Rationale:** Well-defined, SQL-like, and avoids pretending `NOT (wait >= 30)` is true for an unknown wait.
- **Alternatives:** Two-valued "missing ⇒ false" — rejected: under negation it silently turns unknown into true.
- **Status:** Accepted; listed in limitations as a semantic choice the policy owner must confirm.

### D-004 · 2026-11-17 · Exact arithmetic
- **Decision:** All IR numerics are `fractions.Fraction` in canonical field units; Python float literals are converted via `Fraction(repr(x))`.
- **Rationale:** Boundary correctness at inclusive/exclusive thresholds.
- **Status:** Accepted.

### D-005 · 2026-11-17 · Lexicon is data, not code
- **Decision:** Field aliases, destination aliases and named predicates (e.g. "urgent ticket") live in the versioned data-dictionary artifact.
- **Rationale:** The starter text must parse without hardcoding fixture vocabulary; unseen domains must work.
- **Status:** Accepted.

### D-006 · 2026-11-17 · Mixed and/or without parentheses is AMBIGUITY
- **Decision:** No implicit precedence between `and`/`or` in requirement text; bare `between` likewise requires `inclusive`/`exclusive`.
- **Rationale:** English is ambiguous here; the conservative default is to refuse to guess.
- **Status:** Accepted.

### D-007 · 2026-11-17 · Exception semantics
- **Decision:** `Exception to R3: if C, route to D` ≡ `(R3.cond ∧ C) → D`, placed immediately before R3; `R3 ..., unless C` ≡ `R3.cond ∧ ¬C`.
- **Rationale:** "Exception to R3" most naturally scopes to cases R3 would cover.
- **Alternatives:** Exception condition alone placed before R3 — rejected because it would capture cases R3 never covered.
- **Status:** Accepted; documented in `supported_language.md`.

### D-008 · 2026-11-17 · Approval authority and conflict handling
- **Decision:** Only `bundle.json → approvals[]` from roles listed in config count. Supersession is explicit (`supersedes`); timestamps never break ties. Approve+reject on the same change → CONFLICT. Conflicts fork candidate effective policies; an artifact is a blocker only if it mismatches every candidate, otherwise it's INCOMPLETE.
- **Rationale:** The most conservative sound treatment; still reports definite defects despite conflicts.
- **Alternatives:** Latest-timestamp-wins — rejected because it invents a resolution policy that isn't stated anywhere.
- **Status:** **Superseded by D-016 and D-017.** Candidate policies are no longer treated as effective; they are diagnostic-only, and approvals are hash-bound.

### D-009 · 2026-11-17 · Ordering sources
- **Decision:** Requirements: line order. Spec: explicit `priority` field (array order ignored). Workflow: graph structure plus ordered branch lists.
- **Rationale:** Lets "reordered serialized records" be a true no-op for spec JSON.
- **Status:** Accepted.

### D-010 · 2026-11-17 · Restricted Python: parse-only, whitelist interpreter
- **Decision:** `ast.parse` only; no `compile`/`exec`/`eval` of bundle code. Unsupported constructs reachable from the entrypoint → coverage gap. Runtime errors (KeyError, TypeError on `None < 30`) are modeled as outcomes and count as mismatches.
- **Status:** **Superseded by D-019**, which fixes a narrower v1 subset and makes ERROR a distinct outcome type.

### D-011 · 2026-11-17 · Exhaustive vs sampled
- **Decision:** `exhaustive` only when every field is partitionable for every artifact under review and the full cell cross-product (≤ `max_cells`, default 50 000) was evaluated. Otherwise `sampled`, with the region disclosed; a sampled review can never PASS.
- **Status:** **Amended by D-020**, which fixes the exact representative-selection construction and the INTEGER/DECIMAL-scale/CONTINUOUS domain handling, and states the INCOMPLETE-unless-independent-blocker rule.

### D-012 · 2026-11-17 · Differential testing executes only self-authored snippets
- **Decision:** `tests/differential/` and the eval oracle cross-check run CPython in a subprocess on snippets and fixture code authored in this repo, to validate the interpreter. Bundle code is never executed by the reviewer itself.
- **Rationale:** Strongest available check of interpreter fidelity without violating the no-execution rule for reviewed input.
- **Status:** **Accepted** (user-approved 2026-11-17), with the restriction that only trusted repository-authored snippets are executed.

### D-013 · 2026-11-17 · Holdout sealed at end of Stage 1
- **Decision:** H01–H05 plus labels are authored before any verification code exists; `fixtures/holdout/SHA256SUMS` is committed then; holdout is evaluated once at the final checkpoint.
- **Status:** Accepted.

### D-014 · 2026-11-17 · Optional real provider: Gemini via stdlib HTTP
- **Decision:** One optional real client using `urllib` with an env-var key; no SDK dependency. Disabled by default.
- **Status:** **Accepted** (user-approved 2026-11-17) as a provider-neutral adapter with Gemini as one configurable implementation.

### D-015 · 2026-11-17 · Toolchain
- **Decision:** Python 3.12 installed via `uv`; `uv.lock` as the pinned lockfile; ruff + mypy --strict + pytest (+ hypothesis). No FastAPI unless time remains after Stage 4.
- **Rationale:** Only 3.11.9 was installed at planning time; uv 0.12.18 is available.
- **Status:** **Accepted** (user-approved 2026-11-17): `git init` in the verified-empty workspace, 3.12 installation and pinning are authorized.

---

### D-016 · 2026-11-17 · Approvals are cryptographically bound to change content
- **Decision:** Every approval record carries `change_id`, `change_version` and `change_sha256` (canonical JSON hash of the exact change object in the versioned change artifact). All three must match; a changed payload invalidates earlier approvals, which are then treated as unapproved (`INVALID_APPROVAL`). No fuzzy/ID-only carry-forward.
- **Rationale:** Review correction #1; prevents a payload edit from inheriting prior approval.
- **How to apply:** `verityflow.approvals.resolver` recomputes the hash at review time; unit tests edit one byte of a change payload and assert the approval no longer applies.

### D-017 · 2026-11-17 · Conflicts never produce an effective policy
- **Decision:** A conflict (approve+reject on the same change, or unorderable conflicting approved changes) leaves the affected (rule, attribute) semantics **undetermined**. The resolver emits the confirmed base policy plus diagnostic-only variants; no variant is designated effective. Cells whose expected destination differs between variants are undetermined and excluded from blocker establishment. Conflict scope → INCOMPLETE unless an independent blocker exists elsewhere.
- **Rationale:** Review correction #1; the previous "candidate policy" framing risked being read as a confirmed policy.
- **Alternatives rejected:** Timestamp-wins; "apply if any approve"; majority vote — all invent authority.
- **How to apply:** `effective_requirements.candidates[*].diagnostic_only = true`; `undetermined` scopes listed in `unreviewed_scope`.

### D-018 · 2026-11-17 · Four independent check families
- **Decision:** Source requirement coverage (C1), cross-artifact traceability (C2), behavioral equivalence (C3) and evidence completeness (C4) are separate required checks combined only at the status step. Behavioral counterexamples are not the definition of a defect: a requirement with no linked element and no reproducing behavior is `MISSING_REQUIREMENT` (BLOCKER); behavior reproduced without an ID or semantic mapping is `UNTRACEABLE_REQUIREMENT` (COVERAGE_GAP).
- **Rationale:** Review correction #3; missing requirements must be reported when their IDs are absent, while genuinely different IDs are accepted when a `trace_map` mapping agrees.
- **Status:** Accepted; amends D-002.

### D-019 · 2026-11-17 · Conservative Python subset v1 with a distinct ERROR outcome
- **Decision:** v1 allows module docstrings, `__future__`/`typing` no-op imports, literal-only module constants, exactly one entrypoint, `if/elif/else`, `return`, simple name binding, ticket access/`.get`/`in`, single comparisons (`is None` included), `and/or/not`. **No arithmetic, helpers, chained comparisons, loops, comprehensions, lambdas, try, attribute access or other calls.** Unit adaptation is declared in `link.field_map` and performed by the adapter. Runtime errors are the distinct outcome `ERROR(kind, location)`; they never propagate to the reviewer. Nothing in a bundle is ever executed.
- **Rationale:** Review correction #4; anything speculative waits until its semantics are specified and tested.
- **Consequences:** Bundles using helpers or arithmetic → `UNSUPPORTED_CODE` → INCOMPLETE; fixtures B02/B08 adjusted accordingly.
- **Status:** Accepted; supersedes D-010.

### D-020 · 2026-11-17 · Exact partition-representative construction
- **Decision:** Cells are built from the boundary set `B = {min,max} ∪ {c*, prev(c*)} ∪ {v, prev(v), next(v)}` over all comparison cuts `c*` (smallest grid point ≥ c; c itself for continuous domains) and equality/membership literals `v`, with one interior representative per non-empty gap, plus ABSENT/NULL states. Integer (ε=1), decimal-with-scale (ε=10^-s) and continuous rational domains are handled separately; strict comparisons are normalized onto the grid before cut computation. Exhaustive means every cell of the cross-product was evaluated (≤ `max_cells`); otherwise sampled with disclosure and INCOMPLETE unless an independently established blocker exists.
- **Rationale:** Review corrections #5; makes the exhaustiveness claim mathematically checkable and property-testable.
- **How to apply:** `verify/partition.py`; property test compares cell truth vectors with a dense brute-force grid.

### D-021 · 2026-11-17 · Grammar explicitly covers the assignment's starter text; OTHERWISE is ordering sugar
- **Decision:** The parser recognizes `Missing <field> -> ...`, `Otherwise, <named predicate> -> ...`, `<field alias> >= <n> <unit> -> ...` and `All remaining tickets -> ...` exactly as written. `Otherwise` adds no implicit negation and is not stored; precedence is physical line order with first-`TRUE`-match. A catch-all must be last; rules after it, multiple catch-alls, or no-fire-without-default are `AMBIGUITY`. Destination aliases (e.g. `"escalation queue"`) normalize to declared IDs.
- **Rationale:** Review correction #7; the original wording is a Stage 1 unit test, character-for-character.
- **Status:** Accepted.

### D-022 · 2026-11-17 · Evaluation integrity: frozen inputs, both implementations, sealed labels
- **Decision:** Holdout H01–H05 (≥ the required four) are authored and hashed before verification code exists, and are never used for tuning. Final evaluation runs baseline `checkpoint-initial` and the final implementation through the same script, frozen bundle manifest and `configs/eval.toml`, then compares with `compare_reports.py`. Expected labels live outside bundle directories; the reviewer and the model never receive them; tests enforce this. A holdout failure may be fixed but the pre-fix result is preserved and the second holdout run is disclosed. Ablations use dev only.
- **Rationale:** Review correction #6.
- **Status:** Accepted.

### D-023 · 2026-11-17 · P0 setup and Python 3.12 pinning approved
- **Decision:** `git init` in the verified-empty workspace, Python 3.12 installed via `uv` and pinned (`requires-python >=3.12,<3.13`, `.python-version`), differential tests on repository-authored snippets only, and an optional provider-neutral Gemini adapter disabled by default.
- **Status:** Accepted (user-approved 2026-11-17).

### D-024 · 2026-11-17 · `trace_map` is optional; correspondence is inferred from canonical semantics
- **Decision:** Cross-artifact correspondence is computed from semantic descriptors (condition, fields, canonical units, destination, precedence, exception relationship). A supplied `trace_map` is a hint only: it narrows candidates and must pass the same semantic equivalence check; invalid hints are reported (`INVALID_TRACE_HINT`) and ignored. Matching IDs never establish equivalence. Renaming IDs cannot cause a behavioral mismatch because no behavioral check consumes IDs.
- **Ambiguity:** When more than one equally valid counterpart remains and neither semantics, precedence, nor a validated hint disambiguates it, the mapping is reported as `AMBIGUOUS_MAPPING` (`COVERAGE_GAP`, INCOMPLETE). The matcher never arbitrarily selects one.
- **Rationale:** Review requirement #1; avoids ID-matching reviewers while still allowing intentional trace metadata.
- **Status:** Accepted; Stage 1 implements the engine plus spec/workflow descriptors.

### D-025 · 2026-11-17 · Continuous and discrete domain partitioning refined
- **Decision:** Integer domains use adjacent valid integers around every boundary; fixed-scale decimals use adjacent representable values (`±10^-s`); continuous rational domains test each boundary plus a rational midpoint witness inside every open interval. Bounded ends are explicit cells; unbounded ends use `b−ε`/`b+ε` (or `b−1`/`b+1`) representatives. Every cell carries the documented constancy argument: comparisons flip only at `floor(c)`/`ceil(c)` (discrete) or `c` (continuous), equalities only at literals, and Boolean combinations preserve constancy. A review never claims exhaustive coverage while any relevant cell is untested.
- **Rationale:** Review requirement #2.
- **Status:** Accepted; `ir/domains.py` implements the partition construction in Stage 1 and is covered by property tests.

### D-026 · 2026-11-17 · Mapping equivalence is not artifact verification
- **Decision:** The semantic signature used by the mapping engine compares two conditions over the partition induced by their own atoms, for the purpose of correspondence. It is not a verification verdict: it never runs a ticket through an artifact, never produces counterexamples, and cannot establish PASS/CHANGES_REQUIRED. Full behavioral equivalence remains Stage 2.
- **Rationale:** Keeps Stage 1 free of verification code while making correspondence ID-free and semantic.
- **Status:** Accepted.

### D-027 · 2026-11-17 · Code-side descriptors deferred to Stage 2
- **Decision:** Stage 1 provides semantic descriptors for requirements, spec rules and workflow paths. Code descriptors require the restricted-AST layer; until Stage 2, code artifacts in the review target produce `DEFERRED_DESCRIPTOR` (`COVERAGE_GAP`) rather than an assumed mapping.
- **Rationale:** Avoids a second, divergent AST implementation and honours the "no Stage 2 verification code" instruction.
- **Status:** Accepted; revisit when Stage 2's extractor lands.

### D-028 · 2026-11-17 · Effective first-match guard uses an internal `NotTrue` node
- **Decision:** For an ordered list with raw conditions `c_0..c_n`, entry `i`'s compiled condition is `c_i ∧ NotTrue(c_0) ∧ ... ∧ NotTrue(c_{i-1})`. `NotTrue(x)` is TRUE unless `x` is TRUE (UNKNOWN does not count as fired). It is an internal IR node, never accepted from requirement text. Constants fold: `NotTrue(True) → False`, `NotTrue(False) → True`; conjunction folding removes TRUE and short-circuits on FALSE.
- **Why:** Three-valued conditions make naive "negate the previous condition" wrong: `¬UNKNOWN` is UNKNOWN, which would block a later rule even though no earlier rule fired. The `NotTrue` formulation is exact for first-match semantics and makes semantic comparison of ordered lists possible without executing them.
- **How to apply:** `mapping/extract.py` builds guards for requirements, spec rules, spec defaults and workflow branches. Evaluation, normalization, canonical keys, referenced-field collection and approval mutation all recurse through `NotTrue`.
- **Status:** Accepted; covered by IR, evaluation, mapping and approval tests.

### D-029 · 2026-11-17 · Mapping completeness is explicit, and extraction findings propagate
- **Decision:** `map_to_elements` seeds its findings with `extraction.findings`. Unmatched requirements and unmatched elements each produce `UNTRACEABLE_REQUIREMENT` / `UNTRACEABLE_ELEMENT` (`COVERAGE_GAP`). `complete` requires: extraction sound, both unmatched lists empty, and no BLOCKER/COVERAGE_GAP finding.
- **Why:** A silent drop of extraction findings or unmatched entries would let `complete=true` coexist with unreviewed correspondence.
- **Status:** Accepted; `tests/unit/test_mapping.py`.

### D-030 · 2026-11-17 · Supersession cycles are detected on the full edge set
- **Decision:** Build all valid supersession edges first, then drop every edge `(source, target)` for which `target` can reach `source` in that full graph. Self-edges and edges to unknown/invalid approvals are reported and dropped individually.
- **Why:** Incremental edge insertion made the result iteration-order dependent; in a two-approval cycle one edge was dropped but the other still suppressed an approval, hiding a conflict.
- **Status:** Accepted; `tests/unit/test_approvals.py::test_supersession_cycle_is_ignored_with_a_warning`.

### D-031 · 2026-11-17 · Holdout layout and correction disclosure
- **Decision:** The sealed holdout lives in `holdout/bundles/`, expected answers in `holdout/labels/` (outside every bundle), and file hashes in `holdout/MANIFEST.sha256` (one line per file: `sha256`, two spaces, relative path). This replaces the earlier `fixtures/MANIFEST.sha256` / `fixtures/holdout/SHA256SUMS` wording in D-013 and D-022.
- **Correction:** The first holdout run showed `H03_spec_reordered` was generated by shuffling the spec array while leaving priorities unchanged, which is a semantic no-op under D-009. The generator was corrected to swap priority values, artifacts and the manifest were regenerated, and the label was left unchanged. The test-side comparison for `H05` was also corrected to include INFO categories. Both are disclosed in `docs/evaluation_protocol.md` and `docs/debugging_log.md`.
- **Status:** Accepted; `tests/unit/test_holdout_isolation.py` enforces the layout, label isolation and manifest integrity.

### D-032 · 2026-11-17 · External conditions are a separate contract from internal IR
- **Decision:** Specification rules, workflow branches and dictionary named predicates accept `ExternalCondition` (`cmp | eq | ne | in | not_in | missing | and | or | not | const`) from `models/conditions.py`. The internal IR additionally has `NotTrue`, which only the compiler/extractor may emit. `to_ir` converts external composites at ingestion/extraction boundaries.
- **Why:** `NotTrue` encodes ordered first-match guards; allowing it in artifacts would let external input fabricate "no earlier entry fired" semantics. Keeping the layers separate makes the authority boundary explicit and schema-enforceable.
- **How to apply:** Any new artifact-level condition field must use `ExternalCondition`. Any new internal guard must be added to `Expr` only. Negative tests live in `tests/unit/test_conditions.py` and `tests/unit/test_contracts.py`.
- **Status:** Accepted; internal `NotTrue` behaviour is unchanged.

### D-033 · 2026-11-17 · Standalone schemas for the five input surfaces
- **Decision:** Export standalone schemas for anchored requirements (`requirements_source`, `anchored_requirement`), spec components (`spec_rule`, `spec_default`), workflow components (`workflow_node`, `workflow_branch`), linked code (`artifact_entry`, `code_link`) and approvals (`approval_record`). Python artifact entries now require `CodeLink`; artifacts of other kinds may not carry it.
- **Why:** The five inputs were previously implicit in nested models or plain-text parsing. Explicit standalone contracts make versioning, anchors, approval metadata and code provenance independently checkable.
- **How to apply:** `scripts/export_schemas.py` regenerates all 19 schemas; `tests/unit/test_schemas.py` fails on drift; `tests/unit/test_contracts.py` covers versioning, anchors, approval hashes and code links.
- **Status:** Accepted.

### D-034 · 2026-11-17 · Primary threshold fixture is 30 → 25 minutes
- **Decision:** `B07_approved_threshold_25`, `B08_unapproved_threshold_25` and `B09_conflicting_threshold_25` are the primary approval fixtures matching the assignment's 30 → 25 change. A ticket waiting exactly 25 minutes routes to `escalation_queue` under the approved policy and to `normal_queue` under the original policy. The earlier 20-minute `B03`/`B05`/`B06` bundles remain as generalization tests.
- **Why:** The approved example must match the assignment exactly, while retaining coverage of a different replacement value.
- **How to apply:** `tests/unit/test_threshold_change_25.py` evaluates the ordered rules directly with earlier conditions false. A conflicting approval set applies no change, leaves 30 effective and reports `R3.approval` undetermined plus `UNDETERMINED_CONFLICT` (`COVERAGE_GAP`); Stage 1 has no status enum, so INCOMPLETE is represented by undetermined scope and a coverage gap.
- **Status:** Accepted.

### D-035 · 2026-11-17 · H01–H05 reclassified; fresh sealed holdout reserved
- **Decision:** Move H01–H05 unchanged to `fixtures/dev_evidence/holdout_v1/` (bundles, labels and manifest) and treat them as development evidence, because they were inspected during debugging and H03 was regenerated. Author a new sealed set F01–F05 in a claims-triage domain under `holdout/`, freeze labels and `MANIFEST.sha256`, and record the generator hash, bundle/label names, implementation checkpoint `04d9ea6`, docs checkpoint `9f55815` and reservation policy in `holdout/GENERATION.json`.
- **Why:** A holdout that has been read and modified cannot support an unbiased final evaluation. Historical records are preserved rather than erased.
- **How to apply:** `tests/integration/test_dev_evidence_stage1.py` runs the recorded H01–H05 expectations as ordinary regression tests. `tests/unit/test_holdout_isolation.py` verifies the sealed manifest, label isolation and generation metadata, and fails if a development test executes the sealed set. Do not run the pipeline on F01–F05 before final evaluation.
- **Status:** Accepted; the fresh set has not been executed.
