# VerityFlow — submission readiness

**Determination: READY WITH DISCLOSED LIMITATIONS.**

Audit date: 2026-11-18. Source revision measured: `56f5bb49eeb29eff3c2b65e91e3368e5c29f61e7`.
The packaging commit adds documentation and reports only; it does not change `src/`, `tests/`,
fixtures, evaluation data, schemas or sealed artifacts. Machine-readable companion:
`reports/final/submission_readiness.json`.

This determination is not READY because the sealed result remains 8/10 and was not re-measured,
live-model quality is unmeasured, the label audit is AI work by the implementation author rather
than independent human review, and the datasets are small synthetic sets rather than an external
benchmark. None of those gaps is hidden below.

## 1. Requirement traceability

Matrix: `reports/final/requirement_traceability.json` — 27 rows covering all four stages, each with
implementation file, test file, executed test evidence, evaluation evidence and status.

| Status | Rows |
| --- | --- |
| Implemented and tested | 25 |
| Implemented, partially verified | 1 (`S4-BASELINE`: the comparison is produced and schema-validated by the offline runner, but no focused pytest assertion pins its agreement values) |
| Unsupported | 1 (`S3-LIVE-MODEL-QUALITY`: no live model was called) |
| Not implemented | 0 |

Covered topics include source anchors, approval records, missing values, numeric boundaries, nested
conditions, ordered exceptions, revision scope, semantic mapping, missing requirements, spec
verification, workflow reachability, Python AST restrictions, counterexamples, interactions, model
validation, failure handling, status determination, evidence completeness, holdout isolation,
baseline, ablation, debugging regressions, reproducibility and schema/manifest packaging. Every
cited test node was checked against the 411-test collection; none was inferred from documentation.

## 2. Final test results (exact revision `56f5bb4`)

| Gate | Result | Evidence |
| --- | --- | --- |
| pytest | 411 passed in 20.51 s | `reports/submission/offline_verification.log` |
| Ruff check | All checks passed | `reports/submission/ruff_check.log` |
| Ruff format check | 172 files already formatted | `reports/submission/ruff_format_check.log` |
| mypy (strict, `src`) | no issues in 66 source files | `reports/submission/mypy.log` |
| Offline runner | exit 0, all checks passed | `reports/submission/offline_verification.json` |
| JSON schemas | 32 exported, model match, 0 validation errors | `tests/unit/test_schemas.py`, offline run |

The offline verification removed `GEMINI_API_KEY` from the inherited environment; the run still
passed, demonstrating that no credentials are required. Sealed cases were not executed
(`sealed_cases_executed: false`), and the run wrote to a temporary report root so the historical
reports were not overwritten.

## 3. Independent label audit

Report: `reports/remediation/label_audit.json`.

- 22/22 remediation development labels examined and preserved; 0 status, defect-class, threshold or
  applied-change label errors found.
- Every label was re-derived from bundle bytes and cross-checked with `evaluation/oracle.py`.
- Oracle status agreed for 17/22. Four disagreements were resolved manually from documented oracle
  limitations: unit conversion (`G`), candidate-aware conflict (`I`), float literal (`S2`) and code
  execution (`S7`). One bundle (`S5`) was unevaluable by the oracle (`OracleUnsupported` on a
  presence condition).
- Six `tickets` maps contain unscored documentation discrepancies (four stale 29-minute entries at
  effective thresholds 25/24; two mixed-convention entries). No metric consumes `tickets`, no status
  or defect label changes, and the labels were left untouched. No corrected dataset was created
  because no scored label is wrong.
- Auditor identity: Qoder AI assistant, also the implementation author. This is a re-derivation, not
  independent human review, and the report says so.

## 4. Original Stage 4 evaluation (retained, unchanged)

37 bundles: 27 development, 10 sealed. Reports are untouched; hashes are recorded in
`reports/remediation/metrics.json` (`retained_unchanged: true`).

| Development metric | Result |
| --- | --- |
| Overall decisions | 27/27 |
| False PASS | 0/17 |
| False PASS on defective | 0/11 |
| False blocks | 0/10 |
| Findings | 38 total, 11/11 defects identified, 21/21 rule/artifact/version correct |
| Structured evidence | 0/21 (pre-remediation gap) |
| Message witness | 16/21 |
| Valid counterexamples | 78/78 |
| Coverage | 109 requirements, 96 fully / 13 partially reviewed, 3 unsupported expressions |

**Sealed result: 8/10 decisions.** Expected 6 PASS / 2 CHANGES_REQUIRED / 2 INCOMPLETE; actual
5 / 4 / 1. Decision differences: `F03` (PASS → CHANGES_REQUIRED) and `G05` (INCOMPLETE →
CHANGES_REQUIRED). Finding-quality gaps: `G03` returned the correct status and category but its
witness tickets failed a display-string comparison; `G04` was reported as `SCRIPT_DISAGREEMENT`
instead of `THRESHOLD_CHANGED` and its boundary witness failed the same comparison.

Post-evaluation root-cause audit:

- `F03`: the approval hashes exactly the `C1@1` change (5000→3000 usd) while spec `Q3` and workflow
  `r3` still encode 5000, so the two `THRESHOLD_CHANGED` blockers are behaviorally correct. The PASS
  expectation comes from `scripts/run_final_evaluation.py::STAGE1_STATUS`, a Stage 1-only table
  that does not model Stage 2 artifact comparison.
- `G05`: expected INCOMPLETE for an unresolved conflict, but the pre-remediation reviewer verified
  only the confirmed base, so a spec/workflow at 25 became blockers. The remediation conflict pass
  targets this path; no sealed case was rerun.
- `G03`: correct status/category, but `_counterexample_found` compared `requirement_outcome`
  (`"destination 'manual_review'"`) with `"manual_review"`; the comparison can never match.
- `G04`: same comparison defect plus a coarser category.

## 5. Post-remediation development evaluation (separate)

22 new development bundles (A–N approval/revision/conflict, S1–S8 semantic). This is development
evidence, not sealed or unseen performance.

| Metric | Result |
| --- | --- |
| Decisions | 22/22 (6 PASS, 12 CHANGES_REQUIRED, 4 INCOMPLETE) |
| False PASS / false PASS on defective / false block | 0/16 / 0/12 / 0/6 |
| Confirmed findings | 16, with 16/16 structured location, source evidence, implementation evidence, correction and witness |
| Harness identifying findings | 24/24 evidence location and structured evidence; 53/53 valid evidence refs; 22/24 exact message witnesses |
| Counterexamples | 108/108 valid, 0 duplicates, 0 unsupported |
| Approval handling | 14/14 status, 8/8 applied changes, 4/4 conflict handling |
| Semantic bundles | 8/8 primary diagnosis and traces, 51/51 validated witnesses, 8/8 evidence on both sides |
| Coverage | 90 requirements: 74 fully, 16 partially, 16 unreviewed; evidence completeness 90/90; 88 exhaustive comparisons |

## 6. Finding quality, false passes and false blocks

Finding quality improved where it was measured without changing any decision: the retained Stage 4
report has 0/21 structured locations, while the remediated reviewer on the same 27 development
bundles still decides 27/27 and reports 18/18 identifying findings with 37/37 valid evidence refs and
78/78 valid counterexamples. False PASS is 0 in the original development set, the remediation set,
both ablation arms, and no sealed case expected to be non-PASS was returned as PASS. The only
sealed false block relative to its label is `F03`, explained above and retained.

## 7. Coverage and unsupported scope

Unsupported in the product: arbitrary Python beyond the documented AST subset, natural language
outside the anchored grammar, live-model semantic quality, and continuous/unbounded domains that
cannot be exhausted. Sampling and undetermined conflicts are disclosed, not converted to PASS.
Unsupported in evaluation: sealed cases not rerun, no external benchmark, no human label review, and
no live-model measurement.

## 8. Baseline and ablation

- Baseline tag `stage2-baseline` (`4702939fdabfb97a60aa89fd37a70d0678a56996`) ran from a `git
  archive` on identical bundle bytes and config (`identical_inputs: true`). Baseline and final both
  decide 27/27; status agreement 27/27.
- Ablation disables domain partitioning (`configs/no_domain_partitioning.toml`). Exact decisions
  fall from 27/27 to 10/27 (0 PASS, 4 CHANGES_REQUIRED, 23 INCOMPLETE), 63 comparisons become
  unreviewed, and retained counterexamples fall from 78 to 15. False PASS is 0 in both arms because
  the disabled reviewer abstains.

## 9. Debugging evidence

Two real incidents are reproduced from history by running the fix commit's regression test against
an isolated snapshot of the parent: `D-01` (`ace9479` → `528aaf7`) and `D-02` (`528aaf7` →
`f0dbb0c`). Each fails at the parent (exit 1) and passes at the fix and at `HEAD` (exit 0);
`reports/debugging/debugging_examples.json` records the commands and outputs.

## 10. Model integration

Only the fake client was exercised: 17 scenarios, 3 bundles, 51 requests, 30 completed, 21 failed by
design, 18 retries, status invariance true, 0 real model calls. The Gemini adapter exists but fails
closed without credentials and was not called. Fake scenarios demonstrate the advisory contract, not
real-model quality; no accuracy, latency or cost claim is made.

## 11. Documents and page counts

| Document | Limit | Rendered |
| --- | --- | --- |
| `docs/engineering_note.md` | 4 pages | 3 |
| `docs/ai_assistance_note.md` | 1 page | 1 |

Rendering method: Markdown → HTML with a local scratch converter, printed to A4 PDF (15–16 mm
margins, 9.6 pt body) by headless Google Chrome 157.0.8103.79; the page count comes from the PDF
page tree. `README.md` covers purpose, capabilities, architecture, stack, structure, installation,
Python version, dependencies, offline CLI usage, example input, example JSON output, report
interpretation, supported scope, limitations, evaluation methodology and test commands.

## 12. Offline reproducibility

`reports/submission/offline_verification.json` records revision `56f5bb4`, the exact command (a
driver that invokes `scripts/run_offline.main` with a temporary report root), `GEMINI_API_KEY`
removed, exit 0, historical reports not overwritten, and all checks: offline operation, tests,
schema validation, manifest integrity, development evaluation, baseline, ablation, fake models,
debugging regressions and JSON report validation. Raw output is in
`reports/submission/offline_verification.log`.

## 13. Git and manifest integrity

- Source revision: `56f5bb4`; checkpoints `293e92d`, `37766b7`, `56f5bb4`; baseline tag
  `stage2-baseline` (`4702939`) preserved.
- Sealed manifests unchanged (checked in git and by the offline runner):
  - `holdout/MANIFEST.sha256` `e10de058ceb51586f16c657c75e4fd3b4fd9cbded6f76beda9a4e0c648ba6f41`
  - `holdout_stage2/MANIFEST.sha256` `a76fa4bc27dbd28aba49762720ba5292eabcbddbfeb0fe1362a1c6922ae7c3e4`
  - `evaluation/MANIFEST.sha256` `7ac65c0e19665fbbe71c135fd347922aa173eb4e0c7825b5adb54e39eeda2ea8`
  - `evaluation/DATASET.json` `962f092db97baeb33c953a04634f0cd1a6ba07a0388e952db833cb919e4888c7`
- No historical report, label, fixture, sealed artifact or tag was modified.

## 14. Security and packaging

No tracked `.env`, key, certificate, credential file or secret-pattern match (API-key shapes,
private-key headers) was found in tracked content. `uv.lock` is tracked. Caches (`.venv`,
`__pycache__`, pytest/mypy/Ruff/hypothesis caches, `build/`, `dist/`) are ignored and excluded from
the archive. The largest tracked file is 168 KB; all synthetic fixtures, schemas, reports and
manifests are preserved. The archive excludes `.git`; the full history and tags remain in the
repository, and the sealed manifest hashes above are recorded in the reports.

## 15. Remaining risks

1. Sealed performance remains 8/10 and was not re-measured after remediation.
2. Remediation 22/22 is development evidence, not unseen-case accuracy.
3. The label audit is AI re-derivation, not independent human review.
4. Live-model quality is unmeasured; only fake-client contract scenarios exist.
5. Datasets are small and repository-authored, not an external benchmark.
6. Six remediation `tickets` maps carry unscored documentation discrepancies.
7. Two of 18 identifying findings in the Stage 4 rerun have non-exact message witnesses.
8. JSON evidence excerpts are canonical projections, not byte-exact formatting.
9. Page counts were measured with one local Chrome renderer; other renderers may paginate
   differently.

**Final statement.** VerityFlow is ready for submission with these limitations disclosed. The
deterministic reviewer, its evidence, its evaluation separation and its reproducibility are backed
by executed evidence at revision `56f5bb4`; the sealed 8/10 result and the absence of live-model
measurement are not presented as success.
