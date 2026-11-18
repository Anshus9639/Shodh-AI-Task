# VerityFlow — Stage 4 engineering note

## Page 1 — Objective, architecture, scope and core design

VerityFlow is a deterministic-first workflow compliance reviewer. It ingests a versioned bundle
(anchored requirement text, a data dictionary, a structured specification, a webhook workflow and
optionally a linked Python router), resolves hash-bound approvals into the effective
requirements, verifies the linked artifacts against that policy over a disclosed ticket space,
and emits a JSON report whose findings and counterexamples carry evidence. The Stage 4 objective
was not to extend the verifier: it was to evaluate the existing engine on a frozen dataset,
compare it with the Stage 2 baseline, run one controlled ablation, reproduce two real debugging
incidents and submit honest evidence.

The pipeline is layered. Ingestion enforces schemas, byte/count limits and artifact hashes. The
requirement compiler produces an ordered first-match policy in which guard *i* is
`cond_i ∧ ⋀_{j<i} NotTrue(cond_j)`. The approval resolver binds approvals to the exact change
payload by hash, applies only authorized roles and exposes conflicts. Semantic mapping establishes
spec/workflow correspondence, the restricted AST interpreter evaluates the optional router, and
the behaviour evaluator compares requirement, spec, workflow and code outcomes per ticket. A
status is then decided and every unevaluated region is disclosed. An optional model layer can add
advisory observations but cannot change status, findings, approvals or policy.

Scope is deliberately narrow and stated: a restricted requirement grammar with anchored source
text; unique spec priorities; acyclic workflow branches; a single `route(ticket)` Python entry
point restricted to comparisons, boolean logic, literals and terminal returns; optional
`typing`/`__future__` imports. Constructs outside this language are rejected or reported as
`UNSUPPORTED`, never silently approximated. Exact `Fraction` arithmetic and three-valued Kleene
logic avoid float and unknown-value surprises; a missing field is UNKNOWN, and `NotTrue(UNKNOWN)`
is true, which is what makes ordered first-match semantics exact over declared domains.

The status truth table is: a validated blocker → `CHANGES_REQUIRED`; otherwise any incomplete
mandatory check (coverage gap, sampled space, unreviewed scope, unconfirmed policy) →
`INCOMPLETE`; otherwise `PASS`. A blocker must be a validated witness re-routed through both
interpreters, so a difference between artifacts is not enough. `PASS` additionally requires an
exhaustive space and exhaustive comparison modes. This is the core design commitment: report only
what was evaluated, and disclose everything that was not.

## Page 2 — Effective requirements, semantic verification, workflow/code and evidence

The *effective* policy is the ordered policy after approvals. An approval is valid only when its
payload hash matches the change, its role is authorized, its target and timestamp are coherent,
and supersession is acyclic. Conflicting approvals for the same hash without a winner leave the
change undetermined; the resolver then does not emit an effective policy for the affected rules,
and the reviewer abstains instead of guessing. Numeric thresholds carry declared units and are
compared exactly; inclusive/exclusive boundaries are explicit (`>=` versus `>`, `not waiting < 30`
is normalized on the declared integer domain).

Spec and workflow verification is semantic, not identifier-based: renamed IDs do not break
correspondence, and array order is not trusted when explicit priorities exist. A workflow is
reduced to ordered path conditions, with earlier branches negated in later guards. The restricted
AST interpreter records outcomes as destinations, no-match, fall-through, runtime error,
unsupported or step-limit; unsupported regions become unreviewed scope (for example a router
destination not present in the dictionary). Code/workflow comparison is separate from
requirements comparison, so a spec/workflow agreement does not hide a code defect.

Counterexamples are the evidence currency. A witness ticket is only reported as a defect after
being evaluated against the requirement policy and independently re-routed through the artifact;
unvalidated witnesses are downgraded to structural coverage gaps. At most three witnesses are
retained per finding and truncation is disclosed. In the 27-case development suite, 78 of 78
retained counterexamples were validated. Defect identification was 11/11, and every one of the 21
defect findings carried the correct rule reference, artifact ID and artifact version.

Evidence is uneven and this is a measured limitation. Source-anchor and artifact references were
correct, and decision messages contained a concrete witness for 16 of 21 behavioural findings, but
none of the 21 findings in defective cases carried a structured `location` or `evidence` object (0/21). The evidence exists in
the message text and validated counterexamples rather than in the structured fields. That is
reported as a finding-quality gap, not converted into a success claim.

## Page 3 — Evaluation methodology, dataset, baseline and metrics

The frozen evaluation dataset has 37 bundles. The 27 development bundles are 12 B-lineage cases
(B01, B03, B05–B14 used by the repository), the five frozen `H01–H05` development-evidence
bundles, and ten new `E01–E10` bundles covering an approved threshold, missing branch/requirement,
wrong destination, interacting exceptions, unsupported language, unsupported Python syntax,
missing evidence, the unusual `not waiting < 30` formulation, and two metamorphic pairs
(reordered records, paraphrased wording). Expected development outcomes are 10 PASS, 11
CHANGES_REQUIRED and 6 INCOMPLETE. The 10 sealed bundles are F01–F05 (Stage 1) and G01–G05
(Stage 2); all ten were reserved from tuning, and four or more were required to be untouched.

Labels were produced by `evaluation/oracle.py`, a separately written first-match router with its
own three-valued logic, hash-bound approval resolution and probe tickets. It imports none of the
review orchestrator, spec verifier, workflow interpreter, AST interpreter or counterexample
generator. Each label records its method and a short independent reason. Sealed labels live
outside bundles and were read only after the reviewer process finished; expected outcomes never
entered reviewer input. The dataset provenance in `evaluation/DATASET.json` records per-bundle
content hashes, label hashes, source-evidence references, generation seeds/config/generator
hashes, checkpoint and the freeze commit `7f55c5db37b3176f590d6852210078dac56b4b03`; the
development manifest is frozen at SHA-256 `962f092db97baeb33c953a04634f0cd1a6ba07a0388e952db833cb919e4888c7`.

The baseline is tag `stage2-baseline` at `4702939fdabfb97a60aa89fd37a70d0678a56996`, materialized
from git objects without patching (`uv.lock` SHA-256
`9deec83379b4cdf064863795d988923ccdda846a72fc66a4232b06098dedb409`). Baseline and final ran
through the same harness on identical bundle bytes and `configs/default.toml`
(SHA-256 `6e3170f2c12de08a541ac9b447481ed072e12bc1eda5a49e50cb74d813acfd61`). On the 27
development cases both returned 27/27 exact, with 0 false PASS (0/17), 0 false PASS on defective
cases (0/11) and 0 false blocks (0/10); runtime was 18.554 s (final) and 19.441 s (baseline). The
Stage 4 additions therefore changed evaluation evidence, not deterministic decisions. The sealed
set was executed once with the final reviewer only; no sealed baseline run was performed, which is
stated as a limitation rather than backfilled after seeing outcomes.

Sealed decisions were 8/10 (false PASS 0/4, false PASS on defective 0/2, false block 1/6, correct
CHANGES_REQUIRED 2/2, correct INCOMPLETE 1/2). F03 (approved threshold, expected PASS) was blocked
by two `THRESHOLD_CHANGED` findings; G05 (conflicting approvals, expected INCOMPLETE) returned
`CHANGES_REQUIRED` with extra `STRUCTURAL` and `WRONG_DESTINATION` findings. G03's status and
precedence category were right but neither expected counterexample ticket matched the retained
witnesses; G04 was detected as `SCRIPT_DISAGREEMENT` rather than `THRESHOLD_CHANGED` and its
expected boundary witness did not match. Overall, 35 of 37 decisions were correct: 0 false PASS
across 21 non-PASS cases, 0 false PASS on 13 defective cases, one false block on 16 valid cases.
The errors are retained as measured outcomes; no code, label or fixture was changed afterwards.

## Page 4 — Ablation, debugging examples, limitations and reproducibility

The single controlled ablation sets `domain_partitioning.enabled = false`; nothing else changes.
Without partitioning the space is left `UNREVIEWED` instead of being claimed exhaustive: exact
status fell from 27/27 to 10/27 (actual PASS 0, CHANGES_REQUIRED 4, INCOMPLETE 23), abstentions
rose to 10 valid, 7 defective and 6 already-incomplete cases, 63 comparisons became `UNREVIEWED`,
evaluated cells fell from 24,600 to 315 (98.7%), retained counterexamples from 78 to 15, and
runtime from 18.554 s to 17.619 s (startup-dominated). There were still no false PASSes because
the disabled reviewer abstains rather than asserting agreement. This demonstrates the value of
partitioning while respecting the rule that a disabled reviewer must not pretend to be exhaustive.

Two genuine incidents were reproduced from git history, not relabelled. D-01 (`528aaf7`, parent
`ace9479`): a sampled B10 review described an unexercised rule as "shadowed or unsatisfiable"; the
fix commit's assertion failed at the parent (`assert False` in
`test_sampled_space_is_incomplete_and_disclosed`, line 110) and passes at `528aaf7` and HEAD.
D-02 (`f0dbb0c`, parent `528aaf7`): an unsupported router region was found but omitted from the
machine-readable unreviewed scope; the regression test failed at the parent (missing `code` scope
for `ROUTER@1.0`, line 194) and passes at `f0dbb0c` and HEAD. The actual outputs, diagnosis, root
causes, diffs and post-fix results are in `reports/debugging/debugging_examples.json`.

Limitations: the dataset is small and authored in this repository, not an external benchmark; the
sealed set exposed two decision errors and two finding-quality gaps that remain unfixed by design;
behavioural defect findings lack structured locations/evidence; the sealed comparison has no
baseline run; real-model semantic performance was not measured (no Gemini call was made); and the
model layer is advisory by construction, so no AI claim is made about correctness. `UNSUPPORTED`
regions and sampled spaces are disclosed rather than resolved.

Reproducibility: run `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy src`, then the single CPU-only offline command
`uv run python scripts/run_offline.py`. It regenerates every JSON/Markdown report, validates them
against the committed schemas, checks both sealed and development manifests and exits non-zero on a
mandatory failure; it requires no network, credentials or GPU. The sealed evaluation was a separate
one-shot `uv run python scripts/run_final_evaluation.py`, recorded in
`reports/evaluation/holdout_results.json` and `HOLDOUT_REPORT.md`. Both sealed manifests were
verified unchanged before and after the run. The evaluated implementation is commit
`7f55c5d`; the baseline remains `4702939`.
