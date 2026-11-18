# Post-evaluation remediation development summary

Generated from 22 remediation development cases and 27 Stage 4 development cases. Sealed holdouts were not executed.

## Evidence coverage before and after

| Measure | Stage 4 retained (pre-remediation) | Stage 4 dev re-run (post-remediation code) | Remediation dev |
| --- | --- | --- | --- |
| Decisions correct | 27/27 | 27/27 | 22/22 |
| Confirmed BLOCKER findings | 27 of 38 findings | 22 | 16 |
| Structured, resolving locations | 0/21 | 22/22 | 16/16 |
| Validated source evidence | not measured (no evidence items) | 22/22 | 16/16 |
| Validated implementation evidence | not measured (no evidence items) | 22/22 | 16/16 |
| Recommended corrections | not measured (field absent) | 22/22 | 16/16 |
| Message witness | 16/21 | 21/22 | 16/16 |
| Validated counterexamples | 78/78 | 78/78 | 108/108 |
| Duplicate findings | 0 | 0 | 0 |
| False blockers on expected-PASS | 0/10 | 0/10 | 0/6 |

Denominators are not identical. Retained counting scores one identifying finding per defective case; post-remediation runs score every BLOCKER after duplicate merging, so denominators differ where duplicate omission findings were merged.
The retained Stage 4 reports were hashed before and after this run and were not modified.

## Decision accuracy

- Remediation development: 22/22
- Stage 4 development re-run: 27/27

## Finding quality on remediation development

- Confirmed findings: 16
- Structured, resolving locations: 16/16
- Validated source evidence: 16/16
- Validated implementation evidence: 16/16
- Recommended corrections: 16/16
- Validated counterexamples: 108/108
- Duplicate findings: 0
- Unsupported findings: 0
- False blockers on expected-PASS cases: 0/6

## Approval and conflict handling

- Approval case statuses: 14/14
- Applied-change outcomes: 8/8
- Conflict-aware outcomes: 4/4

## Diagnosis on semantic cases

- Primary diagnosis: 8/8
- Specific (non-generic) diagnosis: 8/8
- Evidence on both sides: 8/8

## Retained baseline

The original Stage 4 reports under `reports/final` were not modified. Their finding-quality figures remain the authoritative pre-remediation baseline. That report predates the recommendation field, so its correction coverage is recorded as not measured rather than as zero; the same applies to evidence references, which it never emitted.
