# Evaluation protocol

Stage 1 evaluates contracts, parsing, compilation, approval resolution and semantic mapping. It has no model component, so there is no baseline-vs-final model comparison yet; that is the Stage 2/3 protocol and is unchanged.

## Datasets

| Set | Location | Bundles | Purpose |
| --- | --- | --- | --- |
| Dev | `fixtures/dev/` | `B01_starter`, `B03_approved_change`, `B05_unapproved_change`, `B06_conflicting_approvals` | Development and regression tests; safe to inspect while iterating. |
| Holdout | `holdout/bundles/` | `H01_clean`, `H02_renamed`, `H03_spec_reordered`, `H04_approved_threshold`, `H05_tampered_approval` | Sealed check of generality; run once per checkpoint. |

Labels live in `holdout/labels/`, outside every bundle directory. Bundles contain no `expected` fields, and the reviewer never reads the labels. `holdout/MANIFEST.sha256` lists every holdout bundle file; `tests/unit/test_holdout_isolation.py` recomputes the hashes and also greps `src/` for any reference to the holdout set.

The five holdout bundles exceed the required minimum of four. Each bundle's manifest binds artifact hashes; changing any artifact without regenerating the bundle is a load-time failure.

## Holdout intent

| Bundle | Expected behaviour |
| --- | --- |
| `H01_clean` | Starter requirements, spec and workflow agree; no findings; confirmed base policy. |
| `H02_renamed` | All spec and workflow identifiers renamed; semantic mapping still complete (IDs never decide correspondence). |
| `H03_spec_reordered` | Rule priorities swapped relative to the requirement order; spec mapping incomplete with `UNTRACEABLE_REQUIREMENT` and `UNTRACEABLE_ELEMENT`; workflow unaffected. |
| `H04_approved_threshold` | Approval applies a 30 → 20 minute threshold change on `R3`; confirmed policy reflects 20; no findings. |
| `H05_tampered_approval` | Approval hash does not match the change; change unapproved; `INVALID_APPROVAL` (COVERAGE_GAP) plus `UNAPPROVED_CHANGE` (INFO). |

Note that reordering only the serialized array of spec rules is a semantic no-op under the explicit-`priority` design (D-009). `H03` therefore swaps the priority values themselves.

## Commands

```
# Default suite: excludes the holdout marker.
uv run pytest -q

# Sealed holdout evaluation.
uv run pytest -q -m holdout

# One bundle through the public interface.
uv run verityflow resolve fixtures/dev/B03_approved_change
uv run verityflow map fixtures/dev/B01_starter --artifact all
```

Integration tests (`tests/integration/test_bundle_pipeline.py`) run load → parse → compile → resolve → map on dev bundles and verify evidence anchors with `LineIndex`. The holdout test (`tests/holdout/test_holdout_stage1.py`) compares pipeline output against the frozen labels field by field.

## Current Stage 1 results

- Default suite: **173 passed, 7 deselected**.
- Holdout suite: **7 passed, 173 deselected**.
- `ruff check src tests scripts`: clean.
- `ruff format --check src tests scripts`: clean.
- `mypy src` (strict): clean, 38 source files.

## Integrity disclosure

The first holdout run produced two failures:

1. `H03_spec_reordered` failed mapping. Investigation showed the fixture generator had only shuffled the array order while leaving priorities 1, 2, 3 intact. Under D-009 this is correctly a no-op, so the fixture did not implement its own intent. The generator was corrected to swap the priority values; the spec, bundle and `holdout/MANIFEST.sha256` were regenerated. Labels were not changed.
2. `H05_tampered_approval` failed only in the test helper, which compared gap-severity categories against a label that intentionally includes the INFO `UNAPPROVED_CHANGE` finding. The helper now compares all finding categories. The pipeline output already matched the label.

Both changes are recorded in `docs/debugging_log.md`. This disclosure is required because the holdout was read before its final green run. No implementation behaviour was changed to satisfy a holdout label.
