# AI assistance note

**Scope.** This note covers the work recorded in this repository for Stages 1–4. It separates
tools that were actually invoked from tools that were not, and it makes no claim about AI output
that was not measured.

**Qoder.** Qoder (the agentic coding assistant in Qoder Desktop) was used throughout the recorded
sessions: drafting and editing implementation modules and tests under the author's instructions,
running the test/lint/type/evaluation commands, diagnosing failures, and drafting documentation.
Every code change is in the git history with its commit message and can be reviewed. Stage
boundaries, the corrected sampled-status rule, the dataset design, the oracle, the ablation switch,
the sealed execution and the final submission were approved by the human author before or after
implementation; the assistant did not decide stage transitions on its own.

**ChatGPT.** No ChatGPT-generated code, label, report or metric is present in this repository or
attributed in its commit history. ChatGPT was not used in the recorded sessions that produced this
submission. If the requirements or architecture were discussed with ChatGPT outside those recorded
sessions, that assistance is not represented here, and no measured result in this submission
depends on it.

**Gemini and other providers.** The Gemini adapter is implemented but was not called. No
credentials were supplied, no live request was made, and the ordinary test suite asserts that the
adapter fails closed without a key. The reported `real_model_calls` is 0. Therefore, **real-model
semantic performance was not measured**, and this submission makes no accuracy, latency or cost
claim for any live model.

**Fake scenarios.** The fake model client is synthetic, deterministic replay used for engineering
tests and demonstrations. The offline evaluation exercises all 17 scenarios (including prompt
injection, timeout, rate limit, malformed/truncated/excessive output, retry exhaustion,
fabricated rule/artifact/evidence and a blocker claim) across three bundles: 51 requests, 30
completed, 21 failed as designed, 18 retries, and zero status changes. These numbers describe the
fake client's contract, not real-model behaviour. Fake output never generates labels and never
becomes a finding or blocker.

**How generated code was verified.** The assistant's changes were checked by the repository's own
gates: 375 offline tests, Ruff, Ruff format, strict mypy over `src`, JSON-schema validation of every
machine-readable report, SHA-256 manifest integrity checks, the independent oracle's labels, the
baseline/final comparison on identical inputs, and the one-shot sealed evaluation. The sealed run
found two decision errors and two finding-quality gaps; they are reported unchanged rather than
fixed after the fact.

**How outputs and metrics were checked.** Metrics are computed by `evaluation/harness.py` from the
JSON reports of reviewer subprocesses. Expected labels are applied only after execution and never
enter reviewer input. Every ratio in the reports carries its numerator and denominator; the offline
command exits non-zero if a mandatory check fails. No metric was hand-entered or copied from an
earlier run.

**Human decisions and debugging.** The author specified the requirements, approved each stage,
corrected the sampled-status precedence and the holdout reclassification, and accepted the final
dataset and report set. The two reported incidents were real debugging history
(`528aaf7` and `f0dbb0c`), reproduced against their parent commits with the regression tests from
the fix commits.

**Limits of AI assistance.** No language model independently verified correctness, wrote the
sealed labels, or is the authority for any status or finding. The reviewer's authority is the
deterministic engine plus validated evidence. No AI accuracy number is claimed because none was
measured.
