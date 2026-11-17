# Evaluation protocol

Stage 1 evaluates contracts, parsing, compilation, approval resolution and semantic mapping. It has no model component, so there is no baseline-vs-final model comparison yet; that is the Stage 2/3 protocol and is unchanged.

## Datasets

| Set | Location | Bundles | Purpose |
| --- | --- | --- | --- |
| Dev | `fixtures/dev/` | `B01_starter`, `B03_approved_change`, `B05_unapproved_change`, `B06_conflicting_approvals`, `B07_approved_threshold_25`, `B08_unapproved_threshold_25`, `B09_conflicting_threshold_25` | Development and regression tests; safe to inspect while iterating. `B07`–`B09` are the primary 30 → 25 minute approval fixtures; `B03`/`B05`/`B06` retain the 20-minute generalization case. |
| Development evidence | `fixtures/dev_evidence/holdout_v1/` | `H01_clean`, `H02_renamed`, `H03_spec_reordered`, `H04_approved_threshold`, `H05_tampered_approval` | Frozen historical evidence from Stage 1. These bundles were inspected during debugging, so they are no longer sealed; the recorded labels still run as ordinary regression tests. |
| Sealed holdout | `holdout/bundles/` | `F01_clean`, `F02_renamed`, `F03_approved_threshold`, `F04_unapproved_threshold`, `F05_tampered_approval` | Reserved for final evaluation. Authored before any final-evaluation run and not executed during Stage 1 development or the follow-up corrections. |

Labels live outside every bundle directory (`fixtures/dev_evidence/holdout_v1/labels/` and `holdout/labels/`). Bundles contain no `expected` fields, and the reviewer never reads the labels. Each set has a `MANIFEST.sha256` covering bundles, labels and metadata; `tests/unit/test_holdout_isolation.py` recomputes the sealed holdout hashes, confirms the labels live outside bundles, and fails if any development test calls the pipeline on the sealed set.

The sealed holdout contains five bundles, exceeding the required minimum of four. Each bundle's manifest binds artifact hashes; changing any artifact without regenerating the bundle is a load-time failure. `holdout/GENERATION.json` records the generator script hash, the `configs/default.toml` hash, the reserved bundle/label names, the implementation checkpoint (`04d9ea6`), the documentation checkpoint (`9f55815`) and the reservation policy.

## Sealed holdout intent

The sealed holdout uses a claims-triage domain distinct from the dev ticket-routing fixtures: `segment` (string), `risk` (integer 0–100), `amount` (decimal, USD, scale 2) and `documents` (boolean), routing to `manual_hold`, `fraud_investigation`, `specialist_review` and `standard_payout`.

| Bundle | Expected behaviour |
| --- | --- |
| `F01_clean` | Requirements, spec and workflow agree; no findings; confirmed base policy; route labels match ordered first-match semantics. |
| `F02_renamed` | All spec and workflow identifiers renamed; semantic mapping still complete (IDs never decide correspondence). |
| `F03_approved_threshold` | Approval applies a 5000 → 3000 USD threshold change on `K3`; confirmed policy reflects 3000; no findings. |
| `F04_unapproved_threshold` | Change has no approval; not applied; `K3` stays 5000; `UNAPPROVED_CHANGE` (INFO). |
| `F05_tampered_approval` | Approval hash does not match the change payload; change unapproved; `INVALID_APPROVAL` (COVERAGE_GAP) plus `UNAPPROVED_CHANGE` (INFO). |

## Commands

```
# Default suite: contracts, unit and integration tests. Does not execute the sealed holdout.
uv run pytest -q

# One bundle through the public interface.
uv run verityflow resolve fixtures/dev/B07_approved_threshold_25
uv run verityflow map fixtures/dev/B01_starter --artifact all
```

Integration tests (`tests/integration/test_bundle_pipeline.py`) run load → parse → compile → resolve → map on dev bundles and verify evidence anchors with `LineIndex`. `tests/integration/test_dev_evidence_stage1.py` compares the frozen H01–H05 development evidence against its recorded labels field by field.

A sealed-holdout evaluation test is deliberately not present in Stage 1: the set is reserved for final evaluation, where it will be run once against the final implementation and the recorded labels.

## Current Stage 1 results

- Default suite: **215 passed**.
- Sealed holdout: **not executed** (reserved for final evaluation).
- `ruff check .`: clean.
- `ruff format --check .`: clean.
- `mypy` (strict): clean, 40 source files.

## Development evidence disclosure

The original H01–H05 set was read during Stage 1 debugging and is now reclassified as development evidence. Historical records are preserved rather than erased:

1. `H03_spec_reordered` initially failed mapping. Investigation showed the fixture generator had only shuffled the array order while leaving priorities 1, 2, 3 intact. Under D-009 this is correctly a no-op, so the fixture did not implement its own intent. The generator was corrected to swap the priority values; the spec, bundle and the old holdout `MANIFEST.sha256` were regenerated. Labels were not changed.
2. `H05_tampered_approval` failed only in the test helper, which compared gap-severity categories against a label that intentionally includes the INFO `UNAPPROVED_CHANGE` finding. The helper now compares all finding categories. The pipeline output already matched the label.

Both changes are recorded in `docs/debugging_log.md`. No implementation behaviour was changed to satisfy a label. The sealed F01–F05 set was authored after this reclassification and has not been run.
