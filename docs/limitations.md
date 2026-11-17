# Limitations (Stage 1 and Stage 2)

These are known, intentional boundaries. Anything listed here is either reported as an explicit finding or simply out of scope; none of it is silently assumed correct.

## Scope

- **Behavioural verification is exhaustive only over representative states.** Stage 2 decides PASS / CHANGES_REQUIRED / INCOMPLETE by comparing routing functions over the representative ticket space (see below), not by proving equivalence over every possible input or by symbolically verifying the artifacts.
- **Restricted router subset.** Linked Python is reviewed only when it fits the subset in `docs/supported_language.md`. Anything else is `UNSUPPORTED_CODE` (`COVERAGE_GAP`) and the affected region is unreviewed; it is never executed.
- **No model integration.** Nothing calls a model. No model output can influence parsing, mapping, approvals, verification or any finding.

## Requirement grammar

- One rule per physical line. Line wrapping, tables, nested lists and multi-sentence rules are unsupported.
- The vocabulary is a restricted English subset (see `docs/supported_language.md`). Unsupported normative prose becomes `UNSUPPORTED_LANGUAGE`; it is never guessed.
- Mixed `and`/`or` without parentheses and bare `between` are deliberately ambiguous.
- "Otherwise" is ordering sugar only; the parser does not require it on any entry, and it carries no negation.
- Approval-like sentences in free text are evidence at INFO only (`UNTRUSTED_APPROVAL_CLAIM`); they never grant authority.

## Semantic correspondence

- Correspondence compares conditions over the finite set of states induced by the atoms of both sides, capped at `mapping_max_signature_cells` (4096 by default). Exceeding the cap yields "cannot establish correspondence" (`LIMIT_EXCEEDED`, `COVERAGE_GAP`), not a guess.
- The signature is not a behavioral equivalence proof. It is exact for the atom-induced states it enumerates, but it does not consider artifact execution, stateful behavior, ordering inside workflows beyond the compiled path guards, or unit/link adapters that do not exist yet.
- Precedence is a tie-breaker only when exactly one candidate shares the requirement's precedence. Otherwise the result is `AMBIGUOUS_MAPPING`.
- `exception_of` is carried as metadata; the compiled condition already expresses the exception, so the relation itself is not used to force a match.
- A supplied `trace_map` narrows candidates but cannot create a match that semantics reject. Invalid and unknown hints are findings, not errors in the bundle.

## Specification and workflow artifacts

- Spec ordering is the explicit integer `priority`. Duplicate priorities make the list undetermined: extraction reports `AMBIGUITY`, emits no elements, and mapping is incomplete.
- A spec without a default leaves unmatched inputs; the resulting requirement is `UNTRACEABLE_REQUIREMENT` unless a semantic counterpart exists.
- Workflow traversal is bounded (depth 64). Cycles, missing else-branches, unknown branch targets and non-normalizable branch conditions are `STRUCTURAL` coverage gaps and make extraction unsound.
- Every reachable terminal node contributes a path condition. Large branching graphs can produce many paths; the mapping signature cap protects the matcher, but this is not yet paired with a path-count cap of its own.

## Approvals and changes

- Authority comes only from `bundle.json` approvals whose role is configured, whose referenced change exists, and whose `change_id` + `change_version` + SHA-256 match the exact change payload. Anything else is ignored/reported.
- Supersession is explicit. Cycles are detected against the full edge set and all edges inside a cycle are ignored; no timestamp tie-break exists.
- Conflicts never choose a winner. The confirmed policy is the base policy; conflicting alternatives are diagnostic-only and the affected scope is listed as undetermined.
- Changes targeting an exception rule support only `replace_destination`. Other operations on exception rules are reported unsupported.
- `add_rule` requires an `after` anchor. `remove_rule` can leave a coverage hole in later stages; Stage 1 only records the resulting policy.
- Candidate policies retained after resolution are capped (`max_candidate_policies`, 8); the cap is enforced with disclosure, not silent truncation.

## Domains and exhaustiveness

- Integer, fixed-scale decimal and continuous-rational partitions are implemented and property-tested (`ir/domains.py`), including adjacent representable values, boundary representatives and midpoint witnesses.
- Stage 2 evaluates the cross-product of per-field behavioural states. When the product exceeds `max_cells`, it evaluates a deterministic pairwise sample (baseline, every single-field variation, every field pair) and reports the exact number of skipped combinations. Sampled results are always `INCOMPLETE`; `exhaustive` is never claimed for them.
- `wait_time` is declared `integer` with unit `minutes` in the starter dictionary. `> 29` and `>= 30` are therefore equivalent there, but the equivalence is derived from the declared grid, not assumed: on a scale-2 decimal field the equivalent rewrite is `>= 29.01`, and on a continuous field neither rewrite is equivalent (witnesses 29.5 and 29). `tests/unit/test_verify_domain.py` pins both directions.
- A code-only boundary is added to the space from the router's static literals (unit-converted), so a defect that exists only in code can still produce a witness. Static signature extraction can fail on unusual constructs; that only removes this diagnostic hint, not the behavioural comparison.

## Behavioural verification (Stage 2)

- **Representative states, not all inputs.** Two routers "agree" when they return the same outcome on every evaluated representative. Unenumerated values inside a partition cell are assumed to behave like the cell representative; this is exact for the compiled IR over declared domains, but linked code could in principle branch on values the IR cannot express.
- **One entry point per linked artifact.** The router must define exactly one `route(ticket)` entry point matching `CodeLink.entrypoint`. Helper functions, classes, imports (beyond `typing`/`__future__`), calls, attributes, loops, recursion, mutation and exceptions raised deliberately are rejected or reported as `UNSUPPORTED`/`ERROR`; they are not emulated.
- **Outcome vocabulary.** The interpreter distinguishes `DESTINATION`, `NO_MATCH` (returned `None`), `FALL_THROUGH` (returned nothing), `ERROR` (`KeyError`/`TypeError`/`NameError` at evaluation), `UNSUPPORTED` and `STEP_LIMIT`. Runtime `ERROR` is a finding about the ticket, while `UNSUPPORTED`/`STEP_LIMIT` are unreviewed regions; they are never treated as agreement.
- **Counterexamples are witnesses, not minimal inputs.** Every recorded counterexample is re-routed through both interpreters before it is marked validated. Unvalidated witnesses are downgraded to `STRUCTURAL`/`COVERAGE_GAP` and are not reported as defects. At most three counterexamples are retained per finding; the record discloses truncation.
- **Diagnosis is heuristic over evidence.** `PRECEDENCE`, `THRESHOLD_CHANGED`, `WRONG_OPERATOR`, `MISSING_EXCEPTION` and `SCRIPT_DISAGREEMENT` are chosen from observed behaviour plus static boundary comparison. A difference that fits no pattern is reported as a generic disagreement, never forced into a more specific category.
- **Firing equivalence is narrower than truth equivalence.** First-match guards only need to agree on whether a condition is TRUE, so guard comparisons wrap both sides in `NotTrue`. This lets a code presence guard (`"f" in ticket and ...`) match a plain requirement condition, and it is why an absent-field guard difference alone is not reported as a behavioural defect.
- **Interactions are analysed on the effective policy.** `analyze_interactions` reports rules that never fire in any evaluated ticket and exceptions that fire outside their target. On a sampled space, "never fires" means "never fires in the sample"; the sampling scope is disclosed separately. The requirement compiler conjoins an exception with its target, so misplaced exceptions can only arise from externally supplied or hand-built policies.
- **Coverage is behavioural.** A requirement is covered only if it fires on at least one evaluated ticket and every linked artifact routes all of those tickets to the same destination. Spec rule IDs and workflow node IDs are recorded as evidence handles after the behaviour matched, never as the test.
- **Workflow and spec are single-entry routing functions.** Workflow graphs with alternate paths are reduced to their ordered reachable path conditions; concurrency, timers, retries, state and side effects are not modelled. Spec `priority` values must be unique.
- **Linked code agreement is per router.** Multiple linked routers are each compared with the requirements; they are not differentially compared with each other. An unlinked Python artifact is disclosed as `DEFERRED_DESCRIPTOR`.

## Ingestion and evidence

- Byte and artifact-count limits are enforced before parsing. Exceeding them rejects or discloses exactly what is unreviewed; input is never silently truncated.
- Hashes are recomputed for every declared artifact. A mismatch blocks trust in that artifact.
- Evidence anchors are `TextSpan`s into requirement text and are checked against the loaded file. Spec/workflow findings carry artifact id + version and element id; JSON-pointer locations are defined in the contract but not yet emitted by every finding.
- Python artifacts must declare `CodeLink` provenance (`entrypoint`, parameter name, field and destination maps). Stage 1 only records this contract and emits `DEFERRED_DESCRIPTOR`; it does not inspect the linked code.
- External conditions cannot express the internal `NotTrue` guard. A spec, workflow or dictionary that submits `{"kind": "not_true", ...}` is rejected during artifact validation.

## Evaluation

- The sealed Stage 1 holdout (`holdout/bundles/`, F01–F05) contains five claims-triage bundles, all authored in this repository. It is not an external benchmark and does not establish general accuracy on unseen domain language.
- The sealed Stage 1 holdout has not been executed. Labels grade Stage 1 outputs only (parse counts/gaps, mapping completeness, resolution state, finding categories, route expectations).
- The sealed Stage 2 semantic holdout (`holdout_stage2/bundles/`, G01–G05) returns to the support-ticket domain and grades Stage 2 status, finding category, counterexample and route expectations. Its labels were hand-derived before any Stage 2 algorithm existed; they have not been produced or tuned against this implementation. At least one case (`G03`) depends on precedence over individually correct rules, one on a code/workflow boundary (`G04`), and one on an unresolved approval conflict (`G05`).
- The sealed Stage 2 set has not been executed. The Stage 2 baseline tag `stage2-baseline` (`4702939`) and its configuration are recorded in `docs/evaluation_protocol.md`; the set stays reserved for the final evaluation run.
- Neither sealed set has been used for tuning, and ordinary development tests only hash them and check isolation metadata. The Stage 2 set is frozen at manifest `a76fa4bc27dbd28aba49762720ba5292eabcbddbfeb0fe1362a1c6922ae7c3e4`; the Stage 1 set remains at `e10de058ceb51586f16c657c75e4fd3b4fd9cbded6f76beda9a4e0c648ba6f41`.
- The original H01–H05 set is frozen under `fixtures/dev_evidence/holdout_v1/` as development evidence because it was read during the first green-up run. Its recorded labels now run as ordinary regression tests, and the historical disclosure is preserved in `docs/evaluation_protocol.md` and `docs/debugging_log.md`.
- Fixture provenance separates the implementation baseline (`04d9ea6`) from the commit that generated each frozen set; see [Checkpoint provenance](evaluation_protocol.md#checkpoint-provenance).
