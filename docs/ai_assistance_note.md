# AI assistance note

**Scope.** This note covers the work in this repository for Stages 1–4 and the post-remediation
work. It separates tools that were actually invoked from tools that were not, and it makes no claim
about AI output that was not measured.

**Qoder.** Qoder (the agentic coding assistant in Qoder Desktop) was the coding tool used in the
recorded sessions. It generated and edited repository files: the implementation modules and tests
under `src/verityflow/` and `tests/`, the evaluation harness, oracle, fixture authoring scripts and
runners under `evaluation/` and `scripts/`, the JSON schemas and configs, and the documentation and
reports under `docs/` and `reports/`. It also ran the test, lint, type, evaluation and packaging
commands, diagnosed failures, and drafted the submission documents. Every code change is in the git
history with its commit message. Stage boundaries, the corrected sampled-status rule, the dataset
design, the oracle, the ablation switch, the sealed execution, the remediation plan and the final
submission were approved by the human author; the assistant did not decide stage transitions on its
own.

**ChatGPT.** ChatGPT was used for architecture planning, designing the implementation prompts given
to the coding assistant, reviewing intermediate reports (including the Stage 4 evaluation, the
F03/G03/G05 audit and remediation evaluation), and engineering feedback on scope and methodology. No source
file, fixture, label, report or metric in this repository was directly generated or edited by
ChatGPT, and no commit attributes work to it; its role was advisory and outside the repository.
Any architecture or review conclusions adopted from that discussion were re-implemented and verified
by the coding assistant and the repository's own gates, not accepted on assertion.

**Gemini and other providers.** The Gemini adapter is implemented but was not called. No credentials
were supplied, no live request was made, and the test suite asserts the adapter fails closed without
a key. The reported `real_model_calls` is 0. Therefore **real-model semantic performance was not
measured**, and this submission makes no accuracy, latency or cost claim for any live model.

**Fake scenarios.** The fake model client is synthetic, deterministic replay used for engineering
tests and demonstrations. The offline evaluation exercises all 17 scenarios (including prompt
injection, timeout, rate limit, malformed/truncated/excessive output, retry exhaustion, fabricated
rule/artifact/evidence and a blocker claim) across three bundles: 51 requests, 30 completed, 21
failed as designed, 18 retries, and zero status changes. These numbers describe the fake client's
contract, not real-model behaviour. Fake output never generates labels and never becomes a finding
or blocker.

**How generated code was verified.** The assistant's changes were checked by the repository's own
gates: 411 offline tests, Ruff, Ruff format, strict mypy over `src`, JSON-schema validation of every
machine-readable report, SHA-256 manifest integrity checks, the independent oracle's labels, the
baseline/final comparison on identical inputs, the ablation, and the retained one-shot sealed
evaluation. The sealed run found two decision errors and two finding-quality gaps; they are reported
unchanged rather than fixed after the fact. The remediation changes were verified by new fixtures and
tests plus a 22-bundle development evaluation; sealed cases were not rerun.

**How outputs and metrics were checked.** Metrics are computed by `evaluation/harness.py` from the
JSON reports of reviewer subprocesses. Expected labels are applied only after execution and never
enter reviewer input. Every ratio in the reports carries its numerator and denominator; the offline
command exits non-zero if a mandatory check fails. No metric was hand-entered or copied from an
earlier run. The remediation label audit was an AI re-derivation from bundle bytes cross-checked
against `evaluation/oracle.py`; four oracle limitations and one unsupported evaluation were resolved
manually, and the audit is disclosed as AI work by the implementation author, not human review.

**Human decisions and debugging.** The author specified the requirements, approved each stage,
corrected the sampled-status precedence and the holdout reclassification, accepted the final dataset
and report set, and approved the remediation scope. The two reported incidents are real debugging
history (`528aaf7`, `f0dbb0c`), reproduced against their parent commits with the regression tests from
the fix commits.

**Limits of AI assistance.** No language model independently verified correctness, wrote the sealed
labels, or is the authority for any status or finding. The reviewer's authority is the deterministic
engine plus validated evidence. No AI accuracy number is claimed because none was measured.
