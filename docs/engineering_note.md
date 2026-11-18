# VerityFlow — final engineering note

## 1. Purpose, architecture and supported scope

VerityFlow is a deterministic-first workflow compliance reviewer. It ingests a versioned bundle —
anchored requirement text, a data dictionary, a structured specification, a webhook workflow and an
optional linked Python router — resolves hash-bound approvals into the effective requirements,
verifies each artifact against that policy over a disclosed ticket space, and emits a JSON report
whose findings and counterexamples carry resolvable evidence. The final implementation adds
evidence-backed findings, conflict-aware verification and semantic deduplication to the Stage 1–4
engine. It does not change the historical Stage 4 dataset, labels, sealed bundles or reports.

The pipeline is layered. Ingestion enforces schemas, byte/count limits and artifact hashes. The
requirement compiler produces an ordered first-match policy in which guard *i* is
`cond_i ∧ ⋀_{j<i} NotTrue(cond_j)`. The approval resolver binds approvals to the exact change
payload by hash, applies only authorized roles and isolates conflicts. Semantic mapping establishes
spec/workflow correspondence, the restricted AST interpreter evaluates the optional router, and the
behaviour evaluator compares requirement, spec, workflow and code outcomes per ticket. Status is
decided from evaluated behaviour, and every unevaluated region is disclosed.

Scope is deliberately narrow: a restricted requirement grammar with anchored source text; unique
spec priorities; acyclic workflow branches; one `route(ticket)` Python entry point restricted to
comparisons, boolean logic, literals and terminal returns; optional `typing`/`__future__` imports.
Anything outside that language is rejected or reported as `UNSUPPORTED`, never approximated. Exact
`Fraction` arithmetic and three-valued Kleene logic avoid float and unknown-value surprises. A
missing field is UNKNOWN; `NotTrue(UNKNOWN)` is true, which makes first-match semantics exact over
declared domains. Real live-model quality, arbitrary Python and natural language outside the
documented grammar are unsupported and are never scored as verified.

## 2. Effective requirements, approvals and conflicts

An approval is valid only when its payload hash matches the change, its role is authorized, its
target and timestamp are coherent, and supersession is acyclic. A rejected or stale change grants
no authority. The confirmed policy is the base plus non-conflicted approved changes; a disputed
change is not silently applied.

Conflicts are analyzed rather than collapsed. For every validated counterexample, the reviewer
re-evaluates the confirmed policy and each diagnostic candidate. A difference that is explained by
a permitted approval interpretation becomes `UNDETERMINED_CONFLICT` / `COVERAGE_GAP`, the approval
scope is disclosed, and status cannot rise above `INCOMPLETE` on that basis alone. A defect
reproduced under every permitted interpretation remains a blocker. This is deliberately
conservative: the confirmed base is the only policy that does not invent authority, and it is
presented as one disclosed interpretation, not as the sole truth.

The status truth table is: a validated blocker → `CHANGES_REQUIRED`; otherwise any incomplete
mandatory check (coverage gap, sampled space, unreviewed or undetermined scope) → `INCOMPLETE`;
otherwise `PASS`. `PASS` additionally requires an exhaustive ticket space and exhaustive
comparison modes. A blocker must be a validated witness re-routed through both interpretations;
a textual difference between artifacts is not enough.

## 3. Specification, workflow and code verification

Spec and workflow verification is semantic, not identifier-based: renamed IDs do not break
correspondence, and array position is not trusted when explicit priorities exist. A workflow is
reduced to ordered path conditions, with earlier branches negated in later guards; unreachable
terminals, cycles, fall-through and branch-order defects are distinguished. Code/workflow
comparison is separate from requirements comparison, so spec/workflow agreement cannot hide a code
defect.

The restricted AST interpreter records destinations, no-match, fall-through, runtime error,
unsupported constructs and step/depth/node limits, and it never executes bundle code. Diagnosis uses
exact AST facts — field, operator direction, converted literal and source span — rather than
substring matching, so a complex presence guard does not erase an otherwise exact threshold
diagnosis. Counterexamples retain the requirement and artifact traces, the artifact detail and
evidence references, and are re-routed through both interpreters before being reported. Unvalidated
witnesses are downgraded to structural coverage gaps; truncation at three witnesses per finding is
disclosed.

## 4. Evidence and finding quality

`SourceCatalog` validates every reference against the loaded bytes: line spans through a line
index, spec/workflow JSON pointers against the parsed document, AST spans against retained source,
approval pointers against `bundle.json`. Each `EvidenceReference` records artifact ID, artifact
version, anchor type/value, path, source hash, excerpt and evidence role. Excerpts come from the
artifact; JSON excerpts are canonical projections of the resolved node, never invented text. A
confirmed behavioral finding requires a resolvable requirement-side and implementation-side
reference and a non-empty correction. Structural omissions may carry requirement evidence plus an
explicit missing-implementation statement. A confirmed finding whose evidence cannot be resolved is
downgraded to a disclosed coverage gap, never upgraded to confidence.

Findings are deduplicated by semantic identity plus artifact and evidence anchor, unioning evidence
rather than dropping it; the same defect on two workflow branches remains two evidenced findings.
This changed the measured evidence position without changing any decision: the retained Stage 4
report had 0/21 structured locations and 0/21 structured evidence, while a rerun of the remediated
reviewer on the same 27 development bundles still decides 27/27 correctly and locates 18/18
identifying findings with 37/37 valid evidence references and 78/78 valid counterexamples. The
retained report is not rewritten; both measurements are reported separately.

## 5. Original Stage 4 evaluation (retained, unchanged)

The frozen dataset has 37 bundles: 27 development and 10 sealed. Labels were produced by
`evaluation/oracle.py`, a separately written first-match router that imports no reviewer code;
sealed labels live outside bundles and never enter reviewer input. The original development result
is 27/27 decisions, 0 false PASS and 0 false blocks. The original sealed result is 8/10: two
differences, retained without tuning.

Post-evaluation audit explains the outcomes. The two decision differences are `F03` and `G05`.
`F03` expected PASS, but its approval hashes exactly the `C1@1` change (5000→3000 usd) while spec
rule `Q3` and workflow branch `r3` still encode 5000. Under the effective policy the two
`THRESHOLD_CHANGED` blockers are behaviorally correct; the PASS expectation comes from
`scripts/run_final_evaluation.py::STAGE1_STATUS`, a Stage 1-only status table that does not model
Stage 2 artifact comparison. `G05` expected INCOMPLETE because its approval conflict is unresolved,
but the pre-remediation reviewer verified only the confirmed base, so a spec/workflow at 25 against
the disputed change became `STRUCTURAL`/`WRONG_DESTINATION` blockers; the conflict-analysis pass
added in remediation targets exactly this path and is exercised by development cases I–L, but no
sealed case was rerun. `G03`'s status and precedence category were correct, yet its witnesses were
graded by comparing `requirement_outcome` (`"destination 'manual_review'"`) with the label value
(`"manual_review"`); the comparison can never match, although the retained witness is valid. `G04`
was reported as `SCRIPT_DISAGREEMENT` instead of `THRESHOLD_CHANGED`, and its boundary witness failed
the same comparison. Neither the sealed runner nor any sealed label was changed, and neither sealed
case was rerun during remediation.

The baseline tag `stage2-baseline` (`4702939`) ran unmodified from a `git archive` checkout on
identical bundle bytes and config. Baseline and final both decide 27/27, with 0 false PASS and 0
false blocks. The controlled ablation disables domain partitioning: exact decisions fall from 27/27
to 10/27 (0 PASS, 4 CHANGES_REQUIRED, 23 INCOMPLETE), 63 comparisons become `UNREVIEWED`, and
retained counterexamples fall from 78 to 15. It produces no false PASS because the disabled reviewer
abstains instead of asserting agreement.

Two real incidents are reproduced from git history. D-01 (`528aaf7`, parent `ace9479`): a sampled
review asserted a cause for a rule the sample never exercised; the fix's regression test fails at
the parent and passes at the fix and at HEAD. D-02 (`f0dbb0c`, parent `528aaf7`): an unsupported
router region was omitted from the machine-readable unreviewed scope; its regression test fails at
the parent and passes at the fix and at HEAD. The offline runner re-checks all six outcomes and
fails if any disagrees.

## 6. Post-remediation development evaluation (separate)

The remediation dataset is independent of the sealed cases: 14 approval/revision/conflict bundles
(A–N) and 8 semantic bundles (S1–S8), authored from the four observed weaknesses. The remediated
reviewer decides 22/22 correctly with 0 false PASS and 0/6 false blockers. It produces 16 confirmed
findings with 16/16 structured locations, source evidence, implementation evidence, corrections and
witnesses; 108/108 counterexamples validate; 0 duplicates remain. Approval handling is 14/14,
applied changes 8/8 and conflict handling 4/4: conflict-only `I` and two-conflict `K` are
`INCOMPLETE`, while `J` and `N` keep independent PRECEDENCE/RUNTIME_ERROR blockers with the conflict
disclosed.

An independent audit re-derived every label from bundle bytes and cross-checked it with
`evaluation/oracle.py`: 17/22 statuses agree directly, four differences are attributable to
documented oracle limitations (unit normalization, candidate-aware conflict, float literals, code
execution) and one oracle evaluation is unsupported; manual re-derivation confirms all 22 labels.
Six label `tickets` maps have unscored documentation discrepancies (four stale 29-minute entries at
thresholds 25/24 and two mixed-convention entries); the `tickets` field is not consumed by any
metric, no status or defect label changes, and the original labels are preserved. The audit is an
AI re-derivation by the implementation author, not independent human review. These 22/22 results are
development evidence on new synthetic bundles; they are not sealed performance and do not replace
the 8/10 sealed result.

## 7. AI integration and trust

The optional model layer is advisory by construction. Model output is schema-validated against the
deterministic report (known rule/artifact references, exact anchors and verbatim excerpts) and split
into assisted, unverified and rejected observations. No path promotes a model observation to a
finding, blocker, approval or status. Only synthetic fake-client scenarios were evaluated: 17
scenarios across three bundles, 51 requests, status invariance true, and zero real model calls. The
Gemini adapter exists but was not called; a missing key fails closed before any network request, and
the final offline verification was run with `GEMINI_API_KEY` removed. No accuracy or reliability
claim is made for a live model.

## 8. Limitations and reproducibility

The dataset is small and authored in this repository, not an external benchmark. The sealed result
remains 8/10 and was not re-measured after remediation; no sealed baseline was run. Evidence excerpts
for JSON artifacts are canonical projections rather than byte-exact formatting. The label audit is
not human review. Real-model performance is unmeasured. Unsupported language, sampled spaces and
undetermined conflicts are disclosed rather than resolved.

Reproducibility uses Python 3.12 and the committed `uv.lock`: `uv sync`, then `uv run pytest -q`,
`uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, and the CPU-only
`uv run python scripts/run_offline.py`. The final verification ran at source revision `56f5bb4`
with 411 tests passing, schema-valid reports, both sealed manifests unchanged, and no credentials or
network. The submission commit adds only documentation and reports; it does not alter source,
tests, fixtures, evaluation data or sealed artifacts.
