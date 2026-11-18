# Debugging examples

Reproduced from git history by `scripts/reproduce_debugging_examples.py`.

## D-01 — A sampled review asserted a cause for a requirement the sample never exercised

- Parent commit: `ace94797209f58dca50911851e32247081127bfa`
- Fix commit: `528aaf7615f729979ab157f063571ba5d0005592`
- Regression test: `tests/integration/test_review.py::test_sampled_space_is_incomplete_and_disclosed`
- Parent result: exit 1 in 1.563 s
- Fix result: exit 0 in 1.383 s
- Current HEAD result: exit 0 in 1.708 s

### Actual failing output

```text
F                                                                        [100%]
================================== FAILURES ===================================
_______________ test_sampled_space_is_incomplete_and_disclosed ________________

    def test_sampled_space_is_incomplete_and_disclosed() -> None:
        config = Config(limits=Limits(max_cells=8))
        report = _review("B10_router_clean", config=config)
    
        assert report.status is ReviewStatus.INCOMPLETE
        assert not report.exhaustive
        assert all(record.mode is ComparisonMode.SAMPLED for record in report.comparisons)
        assert any(scope.kind == "sampling" for scope in report.unreviewed)
        assert any(
            check.name == "sampling" and check.outcome.value == "coverage_gap"
            for check in report.checks
        )
        assert "combinations were not evaluated" in report.detail
        interaction = [
            finding for finding in report.findings if finding.category is FindingCategory.INTERACTION
        ]
        assert interaction
>       assert all("is shadowed" not in finding.message for finding in interaction)
E       assert False
E        +  where False = all(<generator object test_sampled_space_is_incomplete_and_disclosed.<locals>.<genexpr> at 0x0000029136081E50>)

tests\integration\test_review.py:110: AssertionError
=========================== short test summary info ===========================
FAILED tests/integration/test_review.py::test_sampled_space_is_incomplete_and_disclosed
1 failed in 0.69s
```

### Diagnosis

The sampled B10 review emitted an INTERACTION/COVERAGE_GAP finding whose message said the unexercised rule 'is shadowed by earlier requirements or cannot be satisfied'. An eight-cell sample of a 2,400-cell space cannot establish either cause. The added assertion rejected the shadow language and required the message to say the rule was not exercised and its firing is unreviewed; the assertion failed against the parent source.

### Root cause

analyze_interactions formatted one fixed message for every never-fired rule. The exhaustive case genuinely observes that a rule never fires in the whole space, but the sampled case only observes absence from the evaluated sample. The formatter conflated the two, and review.py's interaction UnreviewedScope used the same unconditional wording.

### Code change

```diff
diff --git a/src/verityflow/verify/interactions.py b/src/verityflow/verify/interactions.py
index a1d4c0e..5e75ad6 100644
--- a/src/verityflow/verify/interactions.py
+++ b/src/verityflow/verify/interactions.py
@@ -56,13 +56,18 @@ def analyze_interactions(policy: Policy, space: TicketSpace) -> InteractionAnaly
             if rule.exception_of is not None
             else "the rule"
         )
+        cause = (
+            "is shadowed by earlier requirements or cannot be satisfied"
+            if space.exhaustive
+            else "was not exercised by any sampled ticket combination, so whether it "
+            "can fire is unreviewed"
+        )
         findings.append(
             make_finding(
                 FindingCategory.INTERACTION,
                 Severity.COVERAGE_GAP,
                 f"requirement {rule.rule_ref!r} never fires in any {qualifier} ticket "
-                f"combination; {role} is shadowed by earlier requirements or cannot be "
-                "satisfied",
+                f"combination; {role} {cause}",
                 rule_ref=rule.rule_ref,
                 artifact_id=_REQUIREMENTS,
                 explanation=(
diff --git a/src/verityflow/verify/review.py b/src/verityflow/verify/review.py
index 4b68a4c..4720fb3 100644
--- a/src/verityflow/verify/review.py
+++ b/src/verityflow/verify/review.py
@@ -208,7 +208,11 @@ def review_loaded(
             UnreviewedScope(
                 kind="interaction",
                 description=(
-                    "requirement rules that never fire have no observable behaviour: "
+                    (
+                        "requirement rules that never fire have no observable behaviour: "
+                        if verification.space.exhaustive
+                        else "requirement rules not exercised by the sampled ticket space: "
+                    )
                     + ", ".join(interaction.unreachable_rules)
                 ),
                 rule_refs=interaction.unreachable_rules,
```

## D-02 — An unsupported router region was found but omitted from the unreviewed scope

- Parent commit: `528aaf7615f729979ab157f063571ba5d0005592`
- Fix commit: `f0dbb0c0d3108235630c79f33ae66a48421c6ece`
- Regression test: `tests/integration/test_review.py::test_sampled_blocker_with_unsupported_code_keeps_both_disclosures`
- Parent result: exit 1 in 1.805 s
- Fix result: exit 0 in 1.407 s
- Current HEAD result: exit 0 in 1.568 s

### Actual failing output

```text
F                                                                        [100%]
================================== FAILURES ===================================
______ test_sampled_blocker_with_unsupported_code_keeps_both_disclosures ______

    def test_sampled_blocker_with_unsupported_code_keeps_both_disclosures() -> None:
        # E. A sampled blocker and an unsupported code region coexist: the blocker
        # decides the status, the unsupported region stays disclosed as unreviewed.
        report = _review("B14_router_unsupported", config=Config(limits=Limits(max_cells=12)))
    
        assert report.status is ReviewStatus.CHANGES_REQUIRED
        assert not report.exhaustive
        code = next(item for item in report.comparisons if item.name == "code")
        assert code.mode is ComparisonMode.SAMPLED
        assert any(
            item.validated and item.inputs.get("wait_time") == 30 for item in code.counterexamples
        )
        unsupported = [
            finding
            for finding in report.findings
            if finding.category is FindingCategory.UNSUPPORTED_CODE
        ]
        assert unsupported
        assert all(finding.severity is Severity.COVERAGE_GAP for finding in unsupported)
>       assert any(
            scope.kind == "code" and scope.artifact == "ROUTER@1.0" for scope in report.unreviewed
        )
E       assert False
E        +  where False = any(<generator object test_sampled_blocker_with_unsupported_code_keeps_both_disclosures.<locals>.<genexpr> at 0x00000247D8F44110>)

tests\integration\test_review.py:194: AssertionError
=========================== short test summary info ===========================
FAILED tests/integration/test_review.py::test_sampled_blocker_with_unsupported_code_keeps_both_disclosures
1 failed in 0.83s
```

### Diagnosis

B14_router_unsupported reviewed with max_cells=12 contains a validated wait_time=30 blocker and an undeclared-destination region in the router. At the parent commit the blocker did decide CHANGES_REQUIRED, the validated counterexample was re-routed, and UNSUPPORTED_CODE/COVERAGE_GAP findings were present, but the unsupported region was absent from the machine-readable unreviewed scopes. The added assertion that requires a 'code' scope for ROUTER@1.0 failed against the parent source.

### Root cause

_verify_artifacts appended UNSUPPORTED_CODE findings to the findings list but never added the corresponding UnreviewedScope, so the report's unreviewed list silently omitted a router region the interpreter could not evaluate. The same commit also pinned the blocker-first status precedence - implemented since 4702939 but untested and contradicted by documentation claiming sampled status was capped at INCOMPLETE - with tests A-E.

### Code change

```diff
diff --git a/src/verityflow/verify/review.py b/src/verityflow/verify/review.py
index 4720fb3..ae0b7b2 100644
--- a/src/verityflow/verify/review.py
+++ b/src/verityflow/verify/review.py
@@ -271,6 +271,9 @@ def review_loaded(
         and confirmed is not None
         and all(record.mode is ComparisonMode.EXHAUSTIVE for record in verification.comparisons)
     )
+    # Precedence is deliberate: a validated blocker was observed on an evaluated
+    # ticket and independently re-routed, so unreviewed regions elsewhere cannot
+    # downgrade it. Sampling only caps the status when no blocker was established.
     if blocker:
         status = ReviewStatus.CHANGES_REQUIRED
     elif gap or not exhaustive or unreviewed:
@@ -587,6 +590,22 @@ def _verify_artifacts(
             workflow_ref=workflow_reference,
         )
         code_findings.extend(code_result.findings)
+        unsupported = [
+            finding.message
+            for finding in code_result.findings
+            if finding.category is FindingCategory.UNSUPPORTED_CODE
+        ]
+        if unsupported:
+            unreviewed.append(
+                UnreviewedScope(
+                    kind="code",
+                    description=(
+                        "part of the linked router could not be interpreted: "
+                        + "; ".join(unsupported)
+                    ),
+                    artifact=code_program.artifact_ref,
+                )
+            )
         comparisons.append(code_result.record)
         if code_result.workflow_agreement is not None:
             comparisons.append(code_result.workflow_agreement)
```
