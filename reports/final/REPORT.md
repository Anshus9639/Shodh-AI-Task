# Final development evaluation

- Code revision: `7f55c5db37b3176f590d6852210078dac56b4b03`
- Configuration: `configs/default.toml` sha256 `6e3170f2c12de08a541ac9b447481ed072e12bc1eda5a49e50cb74d813acfd61`
- Dataset manifest: sha256 `962f092db97baeb33c953a04634f0cd1a6ba07a0388e952db833cb919e4888c7`
- Model execution mode: off (deterministic; no model requests)
- Bundles: 27

## Decisions

| Status | Expected | Actual |
| --- | --- | --- |
| PASS | 10 | 10 |
| CHANGES_REQUIRED | 11 | 11 |
| INCOMPLETE | 6 | 6 |

- Overall exact status: 27/27
- Correct PASS: 10/10
- Correct CHANGES_REQUIRED: 11/11
- Correct INCOMPLETE: 6/6
- False PASS (any expected non-PASS): 0/17
- False PASS on defective cases: 0/11
- False blocks (valid case reported CHANGES_REQUIRED): 0/10
- Abstentions by expected status: {'PASS': 0, 'CHANGES_REQUIRED': 0, 'INCOMPLETE': 6}

## Finding quality (expected CHANGES_REQUIRED cases)

- Defect identification: 11/11
- Rule reference valid: 21/21
- Artifact and version: 21/21, 21/21
- Evidence location: 0/21
- Structured evidence: 0/21
- Evidence items valid: 0/0
- Message carries a witness ticket: 16/21
- Valid counterexamples: 78/78
- Blocker severity: 20/21
- Unsupported findings: 3; duplicates: 0

## Coverage and resources

- Effective requirements: 109 (fully 96, partially 13, unreviewed 13)
- Source/spec/workflow/code coverage: 109/109, 102/109, 106/109, 20/24
- Evidence completeness: 109/109
- Comparison modes: {'exhaustive': 63}
- Runtime: 16.98s total, 0.63s mean per bundle (includes process start)
- Input bytes: 138956; evaluated combinations: 24600
- Model requests: 0; real calls: 0

Expected labels come from `evaluation/cases/`, are not reviewer inputs, and were
cross-checked by the independent oracle before this run.

Execution errors: none
