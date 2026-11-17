# Evaluation protocol

Stage 1 evaluates contracts, parsing, compilation, approval resolution and semantic mapping. It has no model component, so there is no baseline-vs-final model comparison yet; that is the Stage 2/3 protocol and is unchanged.

## Datasets

| Set | Location | Bundles | Purpose |
| --- | --- | --- | --- |
| Dev | `fixtures/dev/` | `B01_starter`, `B03_approved_change`, `B05_unapproved_change`, `B06_conflicting_approvals`, `B07_approved_threshold_25`, `B08_unapproved_threshold_25`, `B09_conflicting_threshold_25`, `B10_router_clean`, `B11_router_precedence`, `B12_router_threshold`, `B13_router_runtime` | Development and regression tests; safe to inspect while iterating. `B07`–`B09` are the primary 30 → 25 minute approval fixtures; `B03`/`B05`/`B06` retain the 20-minute generalization case. `B10`–`B13` link a Python router: clean, reordered guards, strict boundary and runtime `KeyError`. |
| Development evidence | `fixtures/dev_evidence/holdout_v1/` | `H01_clean`, `H02_renamed`, `H03_spec_reordered`, `H04_approved_threshold`, `H05_tampered_approval` | Frozen historical evidence from Stage 1. These bundles were inspected during debugging, so they are no longer sealed; the recorded labels still run as ordinary regression tests. |
| Sealed Stage 1 holdout | `holdout/bundles/` | `F01_clean`, `F02_renamed`, `F03_approved_threshold`, `F04_unapproved_threshold`, `F05_tampered_approval` | Reserved for final Stage 1 evaluation. Authored before any final-evaluation run and not executed during Stage 1 development or the follow-up corrections. |
| Sealed Stage 2 semantic holdout | `holdout_stage2/bundles/` | `G01_valid_original`, `G02_valid_approved_25`, `G03_precedence_defect`, `G04_code_boundary_defect`, `G05_conflicting_approvals` | Frozen before any Stage 2 algorithm exists; reserved for the Stage 2 semantic evaluation. Never executed or tuned against. |

Labels live outside every bundle directory (`fixtures/dev_evidence/holdout_v1/labels/`, `holdout/labels/` and `holdout_stage2/labels/`). Bundles contain no `expected` fields, and the reviewer never reads the labels. Each set has a `MANIFEST.sha256` covering bundles, labels and metadata; `tests/unit/test_holdout_isolation.py` and `tests/unit/test_stage2_holdout_isolation.py` recompute the sealed hashes, confirm labels live outside bundles, and fail if any development test references the sealed sets. The Stage 2 test also pins the Stage 1 manifest hash `e10de058ceb51586f16c657c75e4fd3b4fd9cbded6f76beda9a4e0c648ba6f41` so the earlier set cannot drift.

The sealed Stage 1 holdout contains five bundles, exceeding the required minimum of four. Each bundle's manifest binds artifact hashes; changing any artifact without regenerating the bundle is a load-time failure. `holdout/GENERATION.json` records the generator script hash, the `configs/default.toml` hash, the reserved bundle/label names, the baseline implementation checkpoint (`04d9ea6`), the documentation checkpoint (`9f55815`) and the reservation policy. See [Checkpoint provenance](#checkpoint-provenance) for what each commit field means.

## Sealed holdout intent

The sealed holdout uses a claims-triage domain distinct from the dev ticket-routing fixtures: `segment` (string), `risk` (integer 0–100), `amount` (decimal, USD, scale 2) and `documents` (boolean), routing to `manual_hold`, `fraud_investigation`, `specialist_review` and `standard_payout`.

| Bundle | Expected behaviour |
| --- | --- |
| `F01_clean` | Requirements, spec and workflow agree; no findings; confirmed base policy; route labels match ordered first-match semantics. |
| `F02_renamed` | All spec and workflow identifiers renamed; semantic mapping still complete (IDs never decide correspondence). |
| `F03_approved_threshold` | Approval applies a 5000 → 3000 USD threshold change on `K3`; confirmed policy reflects 3000; no findings. |
| `F04_unapproved_threshold` | Change has no approval; not applied; `K3` stays 5000; `UNAPPROVED_CHANGE` (INFO). |
| `F05_tampered_approval` | Approval hash does not match the change payload; change unapproved; `INVALID_APPROVAL` (COVERAGE_GAP) plus `UNAPPROVED_CHANGE` (INFO). |

## Stage 2 semantic holdout (G01–G05)

The Stage 2 set returns to the support-ticket routing domain. Requirements use unseen IDs (`T1`–`T4`), spec elements use `TR-*`, and the threshold sentence is one of five vetted equivalent formulations chosen per bundle by seed `20261117` (`>= 30`, `is at least 30`, `> 29`, `not waiting < 30`, `is greater than or equal to 30`). The four base requirements are: missing category → manual review; otherwise urgent ticket → priority queue; otherwise the threshold → escalation queue; all remaining → normal queue.

| Bundle | Construction | Expected |
| --- | --- | --- |
| `G01_valid_original` | Spec and workflow match the ordered requirements at 30 minutes; no change pending. | PASS, no findings, `T3 = 30`. |
| `G02_valid_approved_25` | Hash-bound approval lowers `T3` 30 → 25; spec and workflow both implement 25. | PASS, no findings, `C1@1` applied, `T3 = 25`; wait 25 escalates, wait 24 does not. |
| `G03_precedence_defect` | Every rule is individually equivalent to its requirement, but spec priorities and workflow branches evaluate the wait threshold before missing-category and urgent. | CHANGES_REQUIRED (`PRECEDENCE`); counterexamples: `{wait_time: 45}` → escalation instead of manual review, and `{category: billing, priority: urgent, wait_time: 45}` → escalation instead of priority. |
| `G04_code_boundary_defect` | Spec and workflow use `>= 30`; the linked `router.py` uses `> 30`. | CHANGES_REQUIRED (`THRESHOLD_CHANGED`); counterexample wait = 30 → normal queue instead of escalation. |
| `G05_conflicting_approvals` | Approve and reject bind to the same change hash with no supersession; spec and workflow use 25. | INCOMPLETE (`UNDETERMINED_CONFLICT`); `T3`/`approval` undetermined, no effective policy emitted, threshold-dependent routes undetermined. |

Labels were hand-derived from the requirement order and the change/approval records before any Stage 2 verifier existed, so no implementation output was used to produce them. They live in `holdout_stage2/labels/` and grade status, finding category, counterexample and route expectations. The set is frozen at manifest SHA-256 `a76fa4bc27dbd28aba49762720ba5292eabcbddbfeb0fe1362a1c6922ae7c3e4`; `holdout_stage2/GENERATION.json` records the seed, phrase assignment, generator hash, config hash and checkpoint provenance. Ordinary development tests hash and inspect metadata only; they never load a bundle through the reviewer.

## Commands

```
# Default suite: contracts, unit and integration tests. Does not execute either sealed set.
uv run pytest -q

# Fixture generators (historical inside the frozen sets: do not rerun them against
# holdout/ or holdout_stage2/, because the manifests are content-addressed).
uv run python scripts/build_fixtures.py          # fixtures/dev + sealed Stage 1 holdout
uv run python scripts/build_stage2_holdout.py    # sealed Stage 2 semantic holdout
uv run python scripts/build_stage2_dev_fixtures.py  # fixtures/dev B10-B14 only

# One bundle through the public interface.
uv run verityflow resolve fixtures/dev/B07_approved_threshold_25
uv run verityflow map fixtures/dev/B01_starter --artifact all
uv run verityflow review fixtures/dev/B10_router_clean
```

`verityflow review` prints the full `ReviewReport` as JSON. Exit codes: `0` for PASS, `1` for any other status, `2` for an input/configuration error.

Integration tests (`tests/integration/test_bundle_pipeline.py`) run load → parse → compile → resolve → map on dev bundles and verify evidence anchors with `LineIndex`. `tests/integration/test_review.py` runs the end-to-end reviewer on B01 and B10–B14, asserting status, finding category, validated witness content, sampled-mode disclosure, status precedence and CLI exit codes. `tests/integration/test_dev_evidence_stage1.py` compares the frozen H01–H05 development evidence against its recorded labels field by field.

A sealed-holdout evaluation test is deliberately not present: `F01–F05` are reserved for final Stage 1 evaluation and `G01–G05` for Stage 2, where each set will be run once against the final implementation and its recorded labels.

## Stage 2 baseline

The first complete, runnable deterministic reviewer is commit `4702939` and is tagged `stage2-baseline`. It contains steps 1–8 of the Stage 2 implementation order and the `review` CLI; no tuning followed before the tag was created.

| Fact | Value |
| --- | --- |
| Baseline tag | `stage2-baseline` |
| Baseline commit | `4702939` |
| Implementation provenance checkpoint (Stage 1) | `04d9ea6` |
| Evaluation configuration | `configs/default.toml`, sha256 `6e3170f2c12de08a541ac9b447481ed072e12bc1eda5a49e50cb74d813acfd61` |
| Sealed Stage 2 holdout manifest | `a76fa4bc27dbd28aba49762720ba5292eabcbddbfeb0fe1362a1c6922ae7c3e4` |
| Sealed Stage 1 holdout manifest | `e10de058ceb51586f16c657c75e4fd3b4fd9cbded6f76beda9a4e0c648ba6f41` |

Stage 2 was implemented and tuned only against `fixtures/dev/`. The labels of neither sealed set were loaded, and neither set was executed. The exact run command for the final evaluation is `uv run verityflow review holdout_stage2/bundles/<name>` under the configuration above, compared field by field with `holdout_stage2/labels/<name>.json`.

## Exhaustive and sampled verification

The ticket space is the cross-product of per-field behavioural states induced by every compared boundary (requirement, spec, workflow and code literals). Each field contributes its domain values, absent/null, and one representative on each side of every boundary.

- If the cross-product is at most `max_cells` (50,000), every cell is evaluated and the comparison mode is `EXHAUSTIVE`. Only then can `ReviewReport.exhaustive` be true and the status be PASS.
- Otherwise a deterministic pairwise sample is evaluated (baseline, every single-field variation, every field pair), the exact skipped count is reported, and the comparison mode is `SAMPLED`. Skipped combinations are never described as verified.
- A sampled review can still return `CHANGES_REQUIRED`: a counterexample that was evaluated inside the sample and independently re-routed through both interpreters is an established defect, and unreviewed combinations elsewhere cannot downgrade it. Sampling caps the status at `INCOMPLETE` only when no validated blocker exists. The report keeps `exhaustive = false` and every sampling/unreviewed disclosure in either case.
- In sampled mode, a requirement not exercised by the sample is disclosed as an unreviewed region (`STRUCTURAL`, `COVERAGE_GAP`), not as a defect. A defect still requires a witness that was re-routed and validated.

Tests A–E in `tests/integration/test_review.py` pin the truth table: sampled boundary blocker → `CHANGES_REQUIRED` (`B12`, `max_cells = 12`); sampled without a blocker → `INCOMPLETE` (`B10`, `max_cells = 8`); exhaustive clean → `PASS` (`B10`); exhaustive blocker → `CHANGES_REQUIRED` (`B12`); sampled blocker plus unsupported code → `CHANGES_REQUIRED` with the unsupported region still disclosed (`B14`, `max_cells = 12`).

`tests/integration/test_review.py::test_sampled_space_is_incomplete_and_disclosed` pins this behaviour with `max_cells = 8`.

## Stage 2 development evidence

The B10–B14 fixtures are development evidence, not an evaluation set. Their observed reviewer behaviour (developer fixtures only; no sealed labels were consulted):

| Bundle | Observed | Evidence |
| --- | --- | --- |
| `B10_router_clean` | PASS, exhaustive | code and code-vs-workflow comparisons equal |
| `B11_router_precedence` | CHANGES_REQUIRED, `PRECEDENCE` | validated witness `{category: absent, priority: urgent}`: manual review vs priority queue |
| `B12_router_threshold` | CHANGES_REQUIRED, `THRESHOLD_CHANGED` | validated witness `{wait_time: 30}`: escalation queue vs normal queue |
| `B13_router_runtime` | CHANGES_REQUIRED, `RUNTIME_ERROR` | validated witness `{category: absent, vip: absent}`: `KeyError` in the router |
| `B14_router_unsupported` | CHANGES_REQUIRED, `SCRIPT_DISAGREEMENT` | validated `{wait_time: 30}` witness; the uncatalogued `legacy` destination makes part of the router `UNSUPPORTED_CODE`, disclosed as an unreviewed code region |

## Current results

- Default suite: **306 passed** (Stage 1 baseline 221 retained; Stage 2 adds 85).
- Sealed Stage 1 holdout: **not executed** (reserved for final evaluation).
- Sealed Stage 2 semantic holdout: **not executed** (reserved for final evaluation against `stage2-baseline`).
- `ruff check src tests scripts`: clean.
- `ruff format --check src tests scripts`: clean.
- `mypy` (strict, `src`): clean, 51 source files.

## Checkpoint provenance

Two different facts were previously conflated by the field name `implementation_checkpoint`: the implementation baseline a fixture was authored against, and the commit that actually generated it.

| Fact | Stage 1 holdout (F01–F05) | Stage 2 holdout (G01–G05) |
| --- | --- | --- |
| Implementation baseline the labels target | `04d9ea6` | `04d9ea6` |
| Generation base (HEAD when the generator ran) | `9f55815` | `9a420cd` |
| Commit that first contains the frozen set | `9a420cd` | `b392653` |
| Manifest SHA-256 | `e10de058ceb51586f16c657c75e4fd3b4fd9cbded6f76beda9a4e0c648ba6f41` | `a76fa4bc27dbd28aba49762720ba5292eabcbddbfeb0fe1362a1c6922ae7c3e4` |

`holdout/GENERATION.json` names `04d9ea6` as `implementation_checkpoint`; that is the Stage 1 implementation baseline, not the generation commit (`9a420cd`). The sealed file cannot be edited without changing its preserved manifest hash, so the interpretation is recorded here. `holdout_stage2/GENERATION.json` records the fields separately (`provenance.baseline_implementation_checkpoint`, `provenance.fixture_generation_base_commit`) and points here for the generation commit, because a commit cannot contain its own hash.

## Development evidence disclosure

The original H01–H05 set was read during Stage 1 debugging and is now reclassified as development evidence. Historical records are preserved rather than erased:

1. `H03_spec_reordered` initially failed mapping. Investigation showed the fixture generator had only shuffled the array order while leaving priorities 1, 2, 3 intact. Under D-009 this is correctly a no-op, so the fixture did not implement its own intent. The generator was corrected to swap the priority values; the spec, bundle and the old holdout `MANIFEST.sha256` were regenerated. Labels were not changed.
2. `H05_tampered_approval` failed only in the test helper, which compared gap-severity categories against a label that intentionally includes the INFO `UNAPPROVED_CHANGE` finding. The helper now compares all finding categories. The pipeline output already matched the label.

Both changes are recorded in `docs/debugging_log.md`. No implementation behaviour was changed to satisfy a label. `F01–F05` were authored after this reclassification and have not been run, inspected through the reviewer, modified or regenerated since the freeze; their manifest hash remains `e10de058ceb51586f16c657c75e4fd3b4fd9cbded6f76beda9a4e0c648ba6f41`. The newer `G01–G05` set was authored and frozen in commit `b392653` with hand-derived labels and has not been executed either.
