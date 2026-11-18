# Post-evaluation remediation audit

Scope: the four weaknesses observed in the Stage 4 evaluation. This document was
produced before any remediation code was written. It separates root causes that
were verified by reading the implementation and the retained reports from
hypotheses that still require an independent development reproduction.

The sealed cases `F01-F05` and `G01-G05`, their labels, manifests, seeds and
retained results are not modified and are not used as regression inputs. Their
retained output is used only to identify the category of weakness. Every fix is
developed and measured on independently constructed development cases.

## Baseline preserved

| Checkpoint | Revision |
| --- | --- |
| Stage 1 correction | `9a420cd` |
| Stage 2 baseline tag `stage2-baseline` | `4702939` |
| Stage 2 status correction | `f0dbb0c` |
| Stage 3 model layer | `b63da40` |
| Stage 4 implementation/evaluation | `7f55c5d` |
| Stage 4 documentation/reports | `25f9e2a` |

Stage 4 development baseline retained in `reports/final/metrics.json`:

- 27/27 decisions correct; 0 false PASS; 0 false block.
- 21 identifying findings on 11 defective cases.
- Rule/artifact/artifact-version references: 21/21, 21/21, 21/21.
- Structured locations: 0/21. Structured evidence: 0/21. Valid evidence
  references: 0/0 because no evidence item exists.
- Valid counterexamples: 78/78.
- Duplicate findings: 0 (by the current message-based definition).
- `evidence_location`, `structured_evidence` and `valid_evidence` therefore
  reported 0, not a partial score.

## Verified root causes

| Finding | Actual cause | Affected module | Proposed fix | Regression test |
| --- | --- | --- | --- | --- |
| A1. 0/21 structured locations and 0/21 evidence fields | The verifier layer calls `make_finding` without `location` or `evidence`. Of 44 `make_finding` call sites only the requirement compiler passes an anchor; spec, workflow, code, review, loader, artifact parsing and the approval resolver pass none. | `verify/spec.py`, `verify/workflow.py`, `verify/code.py`, `verify/review.py`, `approvals/resolver.py`, `ingest/artifacts.py`, `ingest/loader.py` | Build a `SourceCatalog` from the loaded bundle and attach typed evidence at each finding site. Populate the legacy `location`/`evidence` fields from the same references for report compatibility. | `tests/unit/test_evidence.py` valid source-line, AST, JSON-pointer, workflow-branch, approval-record cases; `tests/integration/test_review.py` confirmed findings carry resolvable evidence. |
| A2. Evidence objects cannot express provenance | `EvidenceItem` has only `location` and `excerpt`; `Finding` has no evidence role, no source hash and no per-reference artifact identity. | `models/report.py`, `models/common.py` | Add frozen `EvidenceReference` (`artifact_id`, `artifact_version`, `anchor_type`, `anchor_value`, `path`, `source_hash`, `excerpt`, `evidence_role`) and an optional `evidence_refs` tuple on `Finding` and `Counterexample`. Keep existing fields. | `tests/unit/test_evidence.py::test_serialization_round_trip`, schema validation. |
| A3. Spec and workflow artifacts carry no locations | `SpecRule`, `SpecDefault`, `WorkflowNode` and `Branch` are semantic models; the original JSON is available in `LoadedBundle.json` but no pointer index exists. | `models/spec.py`, `models/workflow.py`, `ingest/loader.py` | Resolve rule/node/branch identifiers to `/rules/{i}/when`, `/nodes/{i}/branches/{j}` in the loaded JSON. Validate the pointer against the artifact and keep its SHA-256. | Valid JSON-pointer and workflow-branch tests; invalid pointer and wrong-artifact-version tests must fail closed. |
| A4. Python evidence keeps one line number | `CodeRule` stores only `statement.lineno`; `CodeProgram` keeps the AST but not the source text. Neither end line, column, nor the exact segment is available. | `verify/python_ast.py` | Retain the source in `CodeProgram`; extend `CodeRule` with start/end line, start/end column and the exact source segment from the parsed statement. | `tests/unit/test_evidence.py` valid AST location, fabricated excerpt, invalid line. |
| A5. No finding-quality validation layer | Every report is schema-valid because evidence is optional. Nothing checks that a confirmed blocker has a location, evidence, and a correction. | `verify/review.py`, `schemas.py` | Add `validate_confirmed_findings`: a deterministic confirmed finding requires a valid artifact/version/location, at least one resolvable evidence reference, and a non-empty correction. Behavioral mismatches require requirement-side and implementation-side evidence. Structural omissions may carry source evidence plus an explicit missing-implementation statement. Unsatisfied findings are downgraded to coverage gaps with `MISSING_EVIDENCE` disclosure, never upgraded. | Tests for missing source evidence, invalid version, fabricated excerpt, missing correction; final remediation report counts. |
| B1. `F03` reported as a false block | Verified statically: approval `F3` hashes exactly the `C1@1` change (`1b35dd38...` matches `content_hash`), the change is `REQ@1.0` `K3` `amount >= 5000 usd -> 3000 usd`, and the effective policy therefore uses 3000. Both `spec.json` (`Q3`) and `workflow.json` (`r3`) still encode `>= 5000`, so the two `THRESHOLD_CHANGED` blockers are behaviorally correct. The `PASS` expectation comes from `scripts/run_final_evaluation.py::STAGE1_STATUS`, a manual Stage 1 status table that grades parse/mapping/resolution only and does not model Stage 2 artifact comparisons. This is an evaluation-expectation defect, not a policy-application defect. | `scripts/run_final_evaluation.py` (retained, unchanged), `approvals/resolver.py` (verified correct on this path) | Do not change the sealed runner, label or report. Prove the approved-change path with independent A-J development bundles, including a valid approved 30->25 bundle where every artifact is at 25 and an artifact-not-updated bundle that must block. Document the retained `F03` result as a label-scope limitation. | Remediation cases A, B, C, E, F, G; `tests/integration/test_remediation_approvals.py`. |
| B2. No regression proof for exactly-once application | The resolver planning loop calls `_apply_change` on base rules for candidate planning and then again on the accumulating rule tuple for the confirmed policy. Statically, each non-conflicted change is appended to `ordered` once and applied once, but no test pins this. | `approvals/resolver.py` | Add a change-origin assertion (`origin == change:<id>@<version>`) and a test that the effective threshold equals the last approved value exactly once, with no stale base comparison. | Case H (two sequential changes) and case B; `test_threshold_is_applied_exactly_once`. |
| B3. Unknown coverage for unit conversion and revision binding | `_normalize_payload` converts `to`/`from` into declared field units; `resolve_effective_requirements` compares `base_artifact@base_version` with the review target. Both paths are present, but no fixture covers an approved unit conversion or a wrong artifact revision. | `approvals/resolver.py`, `ir/units.py` | Add a unit-conversion case (25 minutes expressed as seconds/hours) and a wrong-base-version case. Assert the converted value, the applicable revision and the resulting status. | Cases E and G. |
| C1. Conflicts still produce confirmed blockers | `resolve_effective_requirements` always returns a `confirmed` policy (the base plus non-conflicted changes), and `review_loaded` verifies every artifact against that policy. The diagnostic variants and `UndeterminedScope` are never consulted during comparison. An implementation matching the disputed change therefore differs from the confirmed base and becomes a BLOCKER. This is the observed `G05` path. | `approvals/resolver.py`, `verify/review.py`, `verify/spec.py`, `verify/workflow.py`, `verify/code.py` | Add a conflict-analysis pass with a confirmed base, diagnostic candidates and undetermined regions. For every validated counterexample, re-evaluate the confirmed policy and every candidate. Keep a blocker only when every permitted interpretation differs from the artifact. Downgrade differences explained by a candidate to `UNDETERMINED_CONFLICT`/`COVERAGE_GAP`, attach approval evidence, and disclose the scope. | Cases I, J, K, L; `tests/integration/test_remediation_conflicts.py` scenarios 1-7. |
| C2. Conflict uncertainty is not disclosed when a base policy exists | An `approvals` unreviewed scope is added only when `confirmed is None`, which the resolver never produces for a conflict. `UNDETERMINED_CONFLICT` is a COVERAGE_GAP finding, but the status rule still sees the independent blockers. | `verify/review.py` | Add an `approvals`/`conflict` unreviewed scope whenever `effective.undetermined` is non-empty, and include a `conflict_analysis` section on the report. | Status tests: conflict alone -> INCOMPLETE; conflict plus independent defect -> CHANGES_REQUIRED with disclosure. |
| D1. Expected witnesses are compared as display strings | `scripts/run_final_evaluation.py::_counterexample_found` compares `counterexample.requirement_outcome` (for example `"destination 'manual_review'"`) with the label value (`"manual_review"`). The witness itself is valid; the comparison can never match. This explains the failed `G03`/`G04` witness checks. The sealed runner and its report remain unchanged. | `scripts/run_final_evaluation.py` (retained), remediation harness | In the new remediation evaluation compare a normalized destination extracted from the outcome string, and additionally re-execute the witness. Do not alter the retained sealed runner. | `tests/unit/test_remediation_metrics.py` outcome normalization; witness rerun test. |
| D2. Precise diagnosis depends on a complete if/return signature | `verify/code.py::_diagnose` calls `_routing_signature`; `_chain` requires every guard to compile through `_ir_from_ast`. A guard such as `"category" not in ticket or ticket.get("category") is None` contains `Compare(IsNot)` and becomes `None`, so the whole signature is `None` and the fallback is `SCRIPT_DISAGREEMENT`. This is the `G04` path. | `verify/python_ast.py`, `verify/code.py` | Extract numeric comparison facts from the AST independently of the complete routing signature (field, operator, converted literal, source positions). Classify `THRESHOLD_CHANGED`/`WRONG_OPERATOR` only when the requirement and code atoms differ on the declared domain. Use `SCRIPT_DISAGREEMENT` only when no fact supports a more specific cause. | Cases S6/S7; `tests/unit/test_verify_code.py::test_complex_presence_guard_still_names_the_threshold`. |
| D3. Counterexamples do not preserve traces or locations | `Counterexample` stores inputs and two rendered outcomes; `make_counterexample` reroutes but discards `RouteOutcome.trace`/`nodes` and attaches no evidence. A validated flag alone does not show how the outcome was reached. | `verify/routing.py`, `verify/models.py` | Store requirement and artifact traces, the artifact detail, and evidence references on each counterexample. Re-validate by rerouting through both interpreters, as now. | `tests/unit/test_counterexamples.py` trace/location tests; runtime-error witness test. |
| D4. Multiple interactions are capped, not structured | Counterexamples are capped at three per group but grouped only by `(requirement, artifact entry)`; distinct interactions that fall in one group can hide the second witness. | `verify/spec.py`, `verify/workflow.py`, `verify/code.py` | Keep the cap but group by semantic diagnosis and interaction signature; record every retained witness in the comparison record and link it to the finding. | Precedence case with missing-category and urgent witnesses; `test_multiple_precedence_witnesses`. |
| F1. Dedupe identity is message-based | `Finding.finding_id` hashes category, rule, artifact, location and the full message, and messages embed the concrete witness cell. Repeated discovery of the same defect with a different first cell produces distinct IDs; genuinely distinct mismatches on one rule also produce distinct messages. There is no semantic identity to distinguish these. | `models/report.py`, `verify/review.py`, `approvals/resolver.py`, `requirements/compile.py`, `mapping/matcher.py` | Add a semantic identity to `make_finding`; hash category, rule, artifact, version, evidence anchor and semantic identity. Merge duplicate findings by unioning evidence and links rather than dropping the later evidence. Keep artifact identity in the key so spec/workflow/code defects remain distinct. | `tests/unit/test_dedupe.py` repeated threshold, two different mismatches on one rule, distinct artifacts. |

## Hypotheses (not established as causes)

| Hypothesis | Status | How it will be tested |
| --- | --- | --- |
| Approved threshold applied more than once | Not observed statically; resolver appends each non-conflicted change once. | Case H asserts the final condition and `origin`; case B asserts exactly one application. |
| Stale base-policy comparison | The base policy is included only when building the ticket space (`review.py` expressions), not as a comparison target. No evidence of comparison against stale rules. | Cases B/C/H assert the confirmed policy threshold inside the finding message and counterexample. |
| Wrong artifact version selection | The manifest entry version is used for every comparison; `F03` shows `SPEC@1.0`/`WF@1.0`. | Case E binds a change to `REQ@2.0`; case B verifies the artifact version in evidence. |
| Unit normalization bug | `_normalize_payload` converts both `from` and `to`; no failing development case exists. | Case G uses a non-base unit and asserts the effective condition. |
| Duplicate threshold findings are thrown away by a too-aggressive filter | Stage 4 duplicate count is 0 because messages differ, not because a filter removes them. | Semantic dedupe tests and the remediation duplicate metric. |
| Diagnostic variants can be accidentally treated as confirmed | The resolver marks variants `diagnostic_only=True`; review never reads them. The risk is the opposite: confirmed blockers ignore variants. | Conflict cases and the conflict-analysis unit tests. |

## Sealed-case findings and what may be changed

- `F03`: retained result is behaviorally correct under the effective policy
  (approval applied; artifacts still at the old threshold). The `PASS`
  expectation is a Stage 1-only status derivation. Nothing in the sealed bundle
  or label will be changed. The remediation report will state this explicitly
  and will not count it as a reviewer regression or as a corrected error.
- `G03`: witnesses exist but the retained runner's string comparison can never
  match. The new remediation evaluation will use normalized destinations and
  rerun witnesses; the sealed runner is untouched.
- `G04`: the code path demonstrates the signature-completeness weakness, not a
  Python threshold that is actually absent. The fix is an AST-fact diagnosis
  developed on independent cases.
- `G05`: the conflict path is real and is addressed by candidate-aware
  verification. The sealed case will not be rerun during remediation.

## Focused implementation plan

1. **Evidence layer.** New `models/evidence.py` and `evidence.py`:
   `EvidenceRole`, `AnchorType`, `EvidenceReference`, `SourceCatalog`,
   `attach_evidence`, `validate_confirmed_findings`, `dedupe_findings`.
   `SourceCatalog` validates line spans with `LineIndex`, JSON pointers against
   the loaded document, AST spans against the retained source, and approval
   pointers against `bundle.json`. All excerpts come from the artifact; JSON
   excerpts are canonical projections of the resolved node, never invented text.
2. **Finding assembly.** Attach requirement-side evidence from
   `Policy.Rule.anchor`; implementation-side evidence from the spec rule JSON
   pointer, workflow branch pointer, Python AST segment, or approval record.
   Populate legacy `location`/`EvidenceItem` from the first reference. Add
   corrections for every behavioral category and an explicit
   missing-implementation statement for omissions.
3. **Validation.** Run `validate_confirmed_findings` before the report is
   returned. A confirmed blocker without valid evidence/correction becomes a
   `MISSING_EVIDENCE`/`COVERAGE_GAP` finding and an unreviewed region; status
   can only fall to INCOMPLETE, never rise.
4. **Approved revisions.** Add independent bundles A-J under
   `fixtures/remediation/approvals` with labels under
   `evaluation/remediation/cases`. No sealed ticket input is copied. Assert
   original/effective threshold, approval authority, applicable revision,
   expected routes, status and evidence for each case.
5. **Conflict analysis.** New `verify/conflict.py` with `ConflictAnalysis`
   fields `affected_change_id`, `affected_rule`, `conflicting_approval_ids`,
   `confirmed_base`, `diagnostic_variants`, `affected_conditions`,
   `independent_findings`, `undetermined_scope`. Re-evaluate each validated
   witness under the confirmed policy and every diagnostic candidate. Downgrade
   only conflict-dependent differences; preserve blockers that all permitted
   interpretations reproduce. Add an `approvals` unreviewed scope and report
   section.
6. **Counterexample and diagnosis quality.** Extend `Counterexample` with
   traces and evidence. Add AST numeric facts to `CodeProgram`; classify from
   facts, not from message scanning. Add multiple-witness retention. New
   semantic cases S1-S10.
7. **Dedupe.** Add semantic identity to `make_finding` and a shared
   evidence-merging dedupe. Keep artifact identity in the key.
8. **Evaluation.** New remediation bundles and labels, an independently
   authored oracle for those bundles, and `scripts/run_remediation_evaluation.py`
   writing only `reports/remediation/*`. The existing development suite is
   executed by `pytest`; the Stage 4 report files are not rewritten.
9. **Gates.** `uv run pytest -q`, `uv run ruff check .`,
   `uv run ruff format --check .`, `uv run mypy`, and the remediation
   development evaluation. No sealed runner is invoked.

## Conservative defaults for unclear choices

- A conflict leaves the confirmed policy as the base plus non-conflicted
  approved changes. This is the only interpretation that does not invent
  authority; it is disclosed, never presented as the sole truth.
- A finding whose evidence cannot be resolved is downgraded, not suppressed.
  The missing region is disclosed and the finding remains visible.
- Two artifacts producing the same defect remain two findings because artifact
  identity is part of the semantic identity. Only the same artifact + same
  semantic mismatch is merged.
- JSON evidence excerpts are canonical JSON projections of the resolved node,
  with the raw pointer and source hash; they are not presented as byte-exact
  source formatting.
