# Debugging log

Real incidents observed while building Stage 1. Each entry states the symptom, the cause and the fix, with the test that now guards it.

## 2026-11-17 — Workflow branches matched without the first-match guard

- **Symptom:** In a mapping smoke test, requirement `R2` (urgent priority) had no counterpart in the workflow even though the workflow had an equivalent urgent branch.
- **Cause:** `extract_workflow` combined only the branch's own condition with the path condition. A later branch could therefore be TRUE in states where an earlier branch had already fired, so its guard was not semantically equal to the requirement's ordered guard.
- **Fix:** Prefix each non-else branch with `NotTrue` factors for all prior branch conditions, exactly as the requirement compiler does; the else-branch negates all prior conditions as before. `_fold_conjunction` removes constants.
- **Guard:** `tests/unit/test_mapping.py::test_workflow_else_branch_matches_the_catch_all`.

## 2026-11-17 — Mapping reported `complete` while requirements were unmatched

- **Symptom:** `MappingResult.complete` was true for a review where a requirement had no counterpart.
- **Cause:** Completeness only looked at extraction soundness and finding severities; unmatched requirement/element lists were not part of the condition and produced no findings.
- **Fix:** Emit `UNTRACEABLE_REQUIREMENT` and `UNTRACEABLE_ELEMENT` (`COVERAGE_GAP`) for unmatched entries and require both lists to be empty for `complete`.
- **Guard:** `tests/unit/test_mapping.py::test_spec_without_an_else_leaves_inputs_unreviewed`.

## 2026-11-17 — Supersession cycles depended on iteration order

- **Symptom:** With A2 approving and A3 rejecting the same change, adding `A2 supersedes A3` and `A3 supersedes A2` still produced an APPROVED decision instead of CONFLICT.
- **Cause:** Edges were evaluated while being inserted. The A2→A3 edge was recorded first; when A3→A2 was recognised as a cycle it was dropped, but A3 remained marked superseded by A2.
- **Fix:** Collect all candidate edges first, then drop every edge that participates in a cycle (`_reachable(target, source, full_edges)`). Both cyclic edges are ignored, so both approvals stay active and the conflict is reported.
- **Guard:** `tests/unit/test_approvals.py::test_supersession_cycle_is_ignored_with_a_warning`.

## 2026-11-17 — Extraction findings were lost by the matcher

- **Symptom:** A spec with duplicate priorities extracted no elements and `sound=False`, but the mapping result contained no `AMBIGUITY` finding.
- **Cause:** `map_to_elements` initialized its findings list empty and never copied `extraction.findings`.
- **Fix:** Seed the result findings with the extraction findings; completeness already required `extraction.sound`.
- **Guard:** `tests/unit/test_mapping.py::test_duplicate_spec_priorities_are_an_ambiguity`.

## 2026-11-17 — `parse_condition_text` leaked the wrong exception type

- **Symptom:** `parse_condition_text("waiting >= 30 minutes and")` raised `RuleParseError`, not the documented `ConditionTextError`.
- **Cause:** The wrapper converted coverage-gap issues but did not wrap tokenizer or parser exceptions.
- **Fix:** Catch `TokenizeError` and `RuleParseError` and re-raise as `ConditionTextError`, preserving the message.
- **Guard:** `tests/unit/test_parser.py::test_parse_condition_text`.

## 2026-11-17 — Holdout `H03_spec_reordered` did not reorder precedence

- **Symptom:** First holdout run: `H03` expected incomplete spec mapping but mapped completely.
- **Cause:** The fixture generator shuffled the `rules` array while leaving priorities 1, 2, 3 attached to the same rules. Under the explicit-priority design (D-009) serialized array order is a semantic no-op, so the fixture did not implement "reordered".
- **Fix:** Swap the priority values of the first two rules (and keep the shuffled array to also demonstrate order-invariance). Regenerated the spec, bundle hashes and `holdout/MANIFEST.sha256`. The label was unchanged.
- **Disclosure:** The holdout was read before its final green run; this is recorded in `docs/evaluation_protocol.md`.
- **Guard:** `tests/holdout/test_holdout_stage1.py::test_holdout_expectations[H03_spec_reordered]`, plus `tests/unit/test_mapping.py::test_array_position_does_not_override_explicit_priority`.

## 2026-11-17 — Holdout `H05` comparison ignored INFO findings

- **Symptom:** First holdout run: `H05` produced `INVALID_APPROVAL` only, while its label listed `INVALID_APPROVAL` and `UNAPPROVED_CHANGE`.
- **Cause:** The holdout test collected only BLOCKER/COVERAGE_GAP categories; `UNAPPROVED_CHANGE` is intentionally INFO. Pipeline output already contained both findings.
- **Fix:** Compare all finding categories against the label. No implementation or fixture change.
- **Guard:** `tests/holdout/test_holdout_stage1.py::test_holdout_expectations[H05_tampered_approval]`.

## 2026-11-17 — `H05` label said `invalid_approval`, resolver decision said `unapproved`

- **Symptom:** While authoring the holdout labels, `H05` expected the change decision state `invalid_approval`; the resolver recorded `unapproved`.
- **Cause:** The invalid hash is an approval-level failure (`INVALID_APPROVAL` finding); the change itself simply has no valid approval, so its decision state is `unapproved`.
- **Fix:** Corrected the label before the first holdout run to `decision_states: {"C1": "unapproved"}` with categories `INVALID_APPROVAL` and `UNAPPROVED_CHANGE`.
- **Guard:** same holdout test.

## 2026-11-17 — CLI helpers failed strict mypy with "Missing return statement"

- **Symptom:** `mypy src` rejected `_load_dictionary`, `_load_config` and `_open_bundle`.
- **Cause:** They always raise through `_fail`, but `_fail` was annotated `-> None`.
- **Fix:** Annotate `_fail(...) -> NoReturn`.
- **Guard:** `uv run mypy src` in the documented quality gates.
