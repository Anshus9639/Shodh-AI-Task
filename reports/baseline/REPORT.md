# Baseline versus final

- Baseline tag: `stage2-baseline` -> `4702939fdabfb97a60aa89fd37a70d0678a56996`
- Baseline dependency lock: `uv.lock` sha256 `9deec83379b4cdf064863795d988923ccdda846a72fc66a4232b06098dedb409`
- Final code revision: `7f55c5db37b3176f590d6852210078dac56b4b03`
- Shared configuration sha256: `6e3170f2c12de08a541ac9b447481ed072e12bc1eda5a49e50cb74d813acfd61`
- Dataset manifest sha256: `962f092db97baeb33c953a04634f0cd1a6ba07a0388e952db833cb919e4888c7`

Identical inputs: same bundle bytes, approvals, configuration, domain limits and labels.
The baseline ran unmodified from a `git archive` checkout; it has no `--model` flag and
no domain-partitioning switch, so deterministic review is its only mode.

| Metric | Baseline | Final |
| --- | --- | --- |
| Overall exact status | 27/27 | 27/27 |
| False PASS on defective | 0/11 | 0/11 |
| False blocks | 0/10 | 0/10 |
| Valid counterexamples | 78/78 | 78/78 |
| Effective requirements | 109 | 109 |
| Evaluated combinations | 24600 | 24600 |
| Runtime (s) | 18.07 | 16.98 |

Per-case status disagreements: none

The baseline neither executes nor reports the Stage 3 model layer, which is not part
of these decisions. The development suite therefore measures deterministic parity, not
an improvement; the sealed evaluation provides the outcome check on unseen bundles.
