# Limitations (Stage 1)

These are known, intentional boundaries. Anything listed here is either reported as an explicit finding or simply out of scope; none of it is silently assumed correct.

## Scope

- **No behavioral verification.** Stage 1 does not decide PASS / CHANGES_REQUIRED. Mapping `complete` means "a unique semantic correspondence was established", not "the artifacts agree". Counterexamples, partitioning over artifacts, restricted-Python interpretation and status computation are Stage 2.
- **No code descriptors.** Python artifacts in a review target produce `DEFERRED_DESCRIPTOR` (`COVERAGE_GAP`) from `verityflow map`; their correspondence is unreviewed until the Stage 2 AST extractor lands (D-027).
- **No model integration.** Nothing in Stage 1 calls a model. No model output can influence parsing, mapping, approvals or any finding.

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
- Stage 1 makes **no exhaustiveness claim for a review**, because no review exists yet. When Stage 2 uses these partitions, exhaustive will mean every cell of the cross-product was evaluated within `max_cells`; otherwise the region is disclosed as sampled and the review is INCOMPLETE.

## Ingestion and evidence

- Byte and artifact-count limits are enforced before parsing. Exceeding them rejects or discloses exactly what is unreviewed; input is never silently truncated.
- Hashes are recomputed for every declared artifact. A mismatch blocks trust in that artifact.
- Evidence anchors are `TextSpan`s into requirement text and are checked against the loaded file. Spec/workflow findings carry artifact id + version and element id; JSON-pointer locations are defined in the contract but not yet emitted by every finding.
- Python artifacts must declare `CodeLink` provenance (`entrypoint`, parameter name, field and destination maps). Stage 1 only records this contract and emits `DEFERRED_DESCRIPTOR`; it does not inspect the linked code.
- External conditions cannot express the internal `NotTrue` guard. A spec, workflow or dictionary that submits `{"kind": "not_true", ...}` is rejected during artifact validation.

## Evaluation

- The sealed Stage 1 holdout (`holdout/bundles/`, F01–F05) contains five claims-triage bundles, all authored in this repository. It is not an external benchmark and does not establish general accuracy on unseen domain language.
- The sealed Stage 1 holdout has not been executed. Labels grade Stage 1 outputs only (parse counts/gaps, mapping completeness, resolution state, finding categories, route expectations).
- The sealed Stage 2 semantic holdout (`holdout_stage2/bundles/`, G01–G05) returns to the support-ticket domain and grades future Stage 2 status, finding category, counterexample and route expectations. No Stage 2 verifier exists, so its labels are hand-derived design intent; they have not been checked by any implementation. At least one case (`G03`) depends on precedence over individually correct rules, one on a code/workflow boundary (`G04`), and one on an unresolved approval conflict (`G05`).
- Neither sealed set has been used for tuning, and ordinary development tests only hash them and check isolation metadata. The Stage 2 set is frozen at manifest `a76fa4bc27dbd28aba49762720ba5292eabcbddbfeb0fe1362a1c6922ae7c3e4`; the Stage 1 set remains at `e10de058ceb51586f16c657c75e4fd3b4fd9cbded6f76beda9a4e0c648ba6f41`.
- The original H01–H05 set is frozen under `fixtures/dev_evidence/holdout_v1/` as development evidence because it was read during the first green-up run. Its recorded labels now run as ordinary regression tests, and the historical disclosure is preserved in `docs/evaluation_protocol.md` and `docs/debugging_log.md`.
- Fixture provenance separates the implementation baseline (`04d9ea6`) from the commit that generated each frozen set; see [Checkpoint provenance](evaluation_protocol.md#checkpoint-provenance).
