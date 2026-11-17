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
