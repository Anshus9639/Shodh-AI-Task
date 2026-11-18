"""Write the Stage 4 development evaluation labels (evaluation/cases/*.json).

Every case states a hand-authored expected status, defect classes and scenario
tags. The independent oracle in ``evaluation/oracle.py`` re-derives the same
facts from bundle bytes; the builder refuses to write a label when the two
disagree, so a divergence surfaces as a failure instead of a silent label.

The reviewer under test is never invoked here, and no sealed bundle or sealed
label is read.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evaluation"))

import oracle  # noqa: E402

CASES: list[dict[str, object]] = [
    {
        "bundle_id": "B01_starter",
        "path": "fixtures/dev/B01_starter",
        "status": "PASS",
        "classes": [],
        "reasoning": (
            "Four ordered requirements: missing category -> manual, urgent -> priority, "
            "wait >= 30 minutes -> escalation, all remaining -> normal. The spec encodes the "
            "same four rules in the same order and the workflow branches match. With no change "
            "records every ticket routes identically, so the review passes."
        ),
        "evidence": [
            "requirements.txt lines 1-4 (ordered rules)",
            "spec.json rules S1-S3 plus default",
            "workflow.json start branches b1-b4",
        ],
        "scenarios": ["original valid workflow", "declared units"],
    },
    {
        "bundle_id": "B03_approved_change",
        "path": "fixtures/dev/B03_approved_change",
        "status": "CHANGES_REQUIRED",
        "classes": ["THRESHOLD_CHANGED"],
        "reasoning": (
            "Approval A1 is bound to change C1@1 and its sha256 matches the change record, so the "
            "approved change applies: R3 becomes wait >= 20 minutes. The spec and workflow still "
            "encode >= 30 minutes. A ticket at wait=25 is sent to normal by both artifacts "
            "while the "
            "effective requirement sends it to escalation, so the artifacts are stale."
        ),
        "evidence": [
            "changes.json C1 30 -> 20 minutes",
            "bundle.json approvals A1 (policy_owner, hash-bound)",
            "spec.json rule S3 and workflow.json branch b3 keep 30",
        ],
        "scenarios": ["approved threshold change", "numeric threshold changes", "declared units"],
    },
    {
        "bundle_id": "B05_unapproved_change",
        "path": "fixtures/dev/B05_unapproved_change",
        "status": "PASS",
        "classes": [],
        "reasoning": (
            "Change C1 lowers R3 to 20 minutes but the bundle carries no approval record. The "
            "change therefore grants no authority and is not applied; the effective policy remains "
            "the original >= 30 minutes, which both artifacts implement. The unapproved suggestion "
            "is reported as an observation, not a blocker."
        ),
        "evidence": [
            "changes.json C1 30 -> 20 minutes",
            "bundle.json approvals is empty",
            "spec.json rule S3 keeps 30",
        ],
        "scenarios": ["unapproved threshold suggestion"],
    },
    {
        "bundle_id": "B06_conflicting_approvals",
        "path": "fixtures/dev/B06_conflicting_approvals",
        "status": "INCOMPLETE",
        "classes": ["UNDETERMINED_CONFLICT"],
        "reasoning": (
            "Two active policy-owner approvals for C1@1 disagree: A2 approves and A3 rejects. "
            "Resolving the conflict by choosing either vote would invent authority, so the change "
            "scope is undetermined and the review abstains. The artifacts themselves match the "
            "unaltered base policy."
        ),
        "evidence": [
            "bundle.json approvals A2 approve and A3 reject for C1@1",
            "changes.json C1 30 -> 20 minutes",
        ],
        "scenarios": ["contradictory approvals"],
    },
    {
        "bundle_id": "B07_approved_threshold_25",
        "path": "fixtures/dev/B07_approved_threshold_25",
        "status": "CHANGES_REQUIRED",
        "classes": ["THRESHOLD_CHANGED"],
        "reasoning": (
            "The hash-bound approval applies C1@1, so R3 becomes wait >= 25 minutes. The spec and "
            "workflow still use 30. At wait=25-29 the effective requirement routes to escalation "
            "while both artifacts route to normal: an inclusive-boundary defect caused by a stale "
            "artifact."
        ),
        "evidence": [
            "changes.json C1 30 -> 25 minutes",
            "bundle.json approval hash matches C1@1",
            "spec.json rule S3 keeps 30",
        ],
        "scenarios": [
            "approved threshold change",
            "numeric threshold changes",
            "inclusive versus exclusive boundaries",
        ],
    },
    {
        "bundle_id": "B08_unapproved_threshold_25",
        "path": "fixtures/dev/B08_unapproved_threshold_25",
        "status": "PASS",
        "classes": [],
        "reasoning": (
            "C1 proposes 25 minutes without any approval record. It is not applied, so the "
            "effective threshold stays 30 and the unchanged artifacts agree with it."
        ),
        "evidence": [
            "changes.json C1 30 -> 25 minutes",
            "bundle.json approvals is empty",
        ],
        "scenarios": ["unapproved threshold suggestion", "numeric threshold changes"],
    },
    {
        "bundle_id": "B09_conflicting_threshold_25",
        "path": "fixtures/dev/B09_conflicting_threshold_25",
        "status": "INCOMPLETE",
        "classes": ["UNDETERMINED_CONFLICT"],
        "reasoning": (
            "A8 approves and A9 rejects the same hash-bound C1@1 change to 25 minutes. No active "
            "policy can be selected without inventing authority, so the affected scope is "
            "undetermined and the result is INCOMPLETE."
        ),
        "evidence": [
            "bundle.json approvals A8 approve and A9 reject",
            "changes.json C1 30 -> 25 minutes",
        ],
        "scenarios": ["contradictory approvals", "numeric threshold changes"],
    },
    {
        "bundle_id": "B10_router_clean",
        "path": "fixtures/dev/B10_router_clean",
        "status": "PASS",
        "classes": [],
        "reasoning": (
            "The linked router checks missing category, then urgent priority, then wait >= 30 with "
            "guarded key access, then returns normal. This is the requirement order, the link maps "
            "each router literal to the canonical destination, and the guard is inclusive at 30. "
            "Spec, workflow and Python therefore agree on every evaluated ticket."
        ),
        "evidence": [
            "router.py route guards and returns",
            "bundle.json python link destination_map",
            "requirements.txt rule order",
        ],
        "scenarios": [
            "original valid workflow",
            "Python/workflow agreement",
            "inclusive versus exclusive boundaries",
            "declared units",
        ],
    },
    {
        "bundle_id": "B11_router_precedence",
        "path": "fixtures/dev/B11_router_precedence",
        "status": "CHANGES_REQUIRED",
        "classes": ["PRECEDENCE"],
        "reasoning": (
            "Each router guard is individually sensible, but the urgent guard runs before the "
            "missing-category guard. A ticket with category absent and priority urgent satisfies "
            "both; the requirement order sends it to manual review "
            "while the router sends it to the "
            "priority queue. The defect only appears through the interaction of the two rules."
        ),
        "evidence": [
            "router.py lines 5-6 (urgent before missing category)",
            "requirements.txt R1 before R2",
            "workflow.json start branches b1-b2",
        ],
        "scenarios": [
            "wrong precedence",
            "Python/workflow disagreement",
            "multiple individually plausible rules",
        ],
    },
    {
        "bundle_id": "B12_router_threshold",
        "path": "fixtures/dev/B12_router_threshold",
        "status": "CHANGES_REQUIRED",
        "classes": ["THRESHOLD_CHANGED"],
        "reasoning": (
            "The router uses wait_time > 30 while the requirement is wait_time >= 30. Every ticket "
            "with wait=30 is sent to normal instead of escalation: a strict/inclusive boundary "
            "change that the requirement does not authorize."
        ),
        "evidence": [
            "router.py threshold comparison uses > 30",
            "requirements.txt R3 uses >= 30 minutes",
            "spec.json rule S3 and workflow.json branch b3 use >= 30",
        ],
        "scenarios": [
            "inclusive versus exclusive boundaries",
            "numeric comparison boundary change",
            "Python/workflow disagreement",
        ],
    },
    {
        "bundle_id": "B13_router_runtime",
        "path": "fixtures/dev/B13_router_runtime",
        "status": "CHANGES_REQUIRED",
        "classes": ["RUNTIME_ERROR"],
        "reasoning": (
            "The first guard evaluates ticket['vip'] whenever category is absent, without checking "
            "that vip exists. A ticket with both fields absent raises KeyError, "
            "so the router has no "
            "defined destination for an in-domain ticket; the reachable states are not equivalent "
            "to the requirements."
        ),
        "evidence": [
            "router.py line 5 reads ticket['vip'] unguarded",
            "dictionary.json vip is nullable",
            "requirements.txt R1 covers any missing category",
        ],
        "scenarios": ["runtime error", "Python/workflow disagreement"],
    },
    {
        "bundle_id": "B14_router_unsupported",
        "path": "fixtures/dev/B14_router_unsupported",
        "status": "CHANGES_REQUIRED",
        "classes": ["UNSUPPORTED_CODE", "SCRIPT_DISAGREEMENT", "THRESHOLD_CHANGED"],
        "reasoning": (
            "The router uses > 30 instead of >= 30, so tickets at exactly 30 disagree. It also "
            "returns 'legacy' for technical tickets, but the python link declares no destination "
            "mapping for 'legacy', so those outcomes cannot be translated and are unsupported. The "
            "threshold disagreement is a concrete blocker; "
            "the unmapped destination is disclosed as "
            "a coverage gap rather than guessed."
        ),
        "evidence": [
            "router.py uses > 30 and returns 'legacy'",
            "bundle.json destination_map has no 'legacy' entry",
            "requirements.txt R3 >= 30 and R4 catch-all",
        ],
        "scenarios": [
            "unsupported Python syntax",
            "wrong destinations",
            "numeric comparison boundary change",
        ],
    },
    {
        "bundle_id": "H01_clean",
        "path": "fixtures/dev_evidence/holdout_v1/bundles/H01_clean",
        "status": "PASS",
        "classes": [],
        "reasoning": (
            "The historical clean bundle implements the four ordered rules directly; spec and "
            "workflow carry the same order and destinations and there are no changes."
        ),
        "evidence": [
            "requirements.txt lines 1-4",
            "spec.json rules S1-S3",
            "workflow.json branches b1-b4",
        ],
        "scenarios": ["original valid workflow"],
    },
    {
        "bundle_id": "H02_renamed",
        "path": "fixtures/dev_evidence/holdout_v1/bundles/H02_renamed",
        "status": "PASS",
        "classes": [],
        "reasoning": (
            "Only identifiers changed: spec ids S1-S3 became S-ALPHA..S-GAMMA with scaled "
            "priorities 10/20/30 and workflow branch ids route-1..4. Relative order and semantics "
            "are unchanged, so the review must not fail on renaming."
        ),
        "evidence": [
            "spec.json ids and priorities renamed, order preserved",
            "workflow.json branch ids renamed, order preserved",
        ],
        "scenarios": ["metamorphic: only rule IDs change"],
    },
    {
        "bundle_id": "H03_spec_reordered",
        "path": "fixtures/dev_evidence/holdout_v1/bundles/H03_spec_reordered",
        "status": "CHANGES_REQUIRED",
        "classes": ["PRECEDENCE"],
        "reasoning": (
            "The spec serializes the urgent rule in the priority-1 slot and the missing-category "
            "rule in the priority-2 slot. Priorities are execution order, "
            "so a ticket with category "
            "absent and priority urgent now routes to priority instead of manual review. This is a "
            "real semantic precedence change, not a cosmetic record reorder."
        ),
        "evidence": [
            "spec.json rules array order and priority values 1/2 are swapped",
            "requirements.txt R1 before R2",
        ],
        "scenarios": [
            "metamorphic: actual semantic precedence changes",
            "wrong precedence",
        ],
    },
    {
        "bundle_id": "H04_approved_threshold",
        "path": "fixtures/dev_evidence/holdout_v1/bundles/H04_approved_threshold",
        "status": "CHANGES_REQUIRED",
        "classes": ["THRESHOLD_CHANGED"],
        "reasoning": (
            "A4 is a valid hash-bound approval of C1@1, so R3 becomes wait >= 20 minutes. The spec "
            "still encodes >= 30; tickets in 20..29 route to normal in the artifact and escalation "
            "in the effective requirement."
        ),
        "evidence": [
            "changes.json C1 30 -> 20 minutes",
            "bundle.json approval A4 hash matches",
            "spec.json S3 keeps 30",
        ],
        "scenarios": ["approved threshold change", "numeric threshold changes"],
    },
    {
        "bundle_id": "H05_tampered_approval",
        "path": "fixtures/dev_evidence/holdout_v1/bundles/H05_tampered_approval",
        "status": "INCOMPLETE",
        "classes": ["INVALID_APPROVAL"],
        "reasoning": (
            "The only approval claims C1@1 but its change_sha256 is all zeros and does not match "
            "the change record. It grants no authority, so the change stays unapproved and the "
            "approval evidence is invalid. Review of the approval claim cannot be completed."
        ),
        "evidence": [
            "bundle.json approval A5 with zero hash",
            "changes.json C1 content hash differs",
        ],
        "scenarios": ["invalid approval", "missing evidence"],
    },
    {
        "bundle_id": "E01_valid_approved_25",
        "path": "evaluation/bundles/E01_valid_approved_25",
        "status": "PASS",
        "classes": [],
        "reasoning": (
            "The hash-bound approval applies C1@1 (R3 becomes >= 25 minutes), and both the spec "
            "rule S3 and workflow branch b3 were updated to 25. All artifacts encode the effective "
            "policy, so the review passes. This is the valid counterpart to B07/H04."
        ),
        "evidence": [
            "changes.json C1 30 -> 25 minutes",
            "bundle.json approval hash matches",
            "spec.json S3 and workflow.json b3 use 25",
        ],
        "scenarios": [
            "approved threshold change",
            "numeric threshold changes",
            "declared units",
        ],
    },
    {
        "bundle_id": "E02_missing_branch",
        "path": "evaluation/bundles/E02_missing_branch",
        "status": "CHANGES_REQUIRED",
        "classes": ["MISSING_REQUIREMENT", "MISSING_BRANCH"],
        "reasoning": (
            "The requirements keep the urgent -> priority rule, "
            "but the spec has no rule for it and "
            "the workflow has no urgent branch. "
            "Urgent tickets fall through to escalation or normal. "
            "The missing source requirement is a coverage defect, not an equivalence proof."
        ),
        "evidence": [
            "requirements.txt R2 urgent -> priority queue",
            "spec.json has no rule for priority == urgent",
            "workflow.json start has no urgent branch",
        ],
        "scenarios": ["missing source requirements", "missing branches"],
    },
    {
        "bundle_id": "E03_wrong_destination",
        "path": "evaluation/bundles/E03_wrong_destination",
        "status": "CHANGES_REQUIRED",
        "classes": ["WRONG_DESTINATION"],
        "reasoning": (
            "Both artifacts contain the urgent condition, but its destination is normal_queue "
            "instead of priority_queue. Every urgent ticket is misrouted "
            "while the rule shape looks "
            "correct."
        ),
        "evidence": [
            "requirements.txt R2 -> priority queue",
            "spec.json S2 destination normal_queue",
            "workflow.json priority terminal destination normal_queue",
        ],
        "scenarios": ["wrong destinations"],
    },
    {
        "bundle_id": "E04_interacting_exceptions",
        "path": "evaluation/bundles/E04_interacting_exceptions",
        "status": "CHANGES_REQUIRED",
        "classes": ["MISSING_EXCEPTION"],
        "reasoning": (
            "The requirements place an exception to R3 before R3: "
            "a vip ticket with wait >= 30 goes "
            "to priority instead of escalation. The spec instead appends a general vip rule after "
            "the wait rule. Each rule looks plausible alone, but a vip ticket with wait >= 30 "
            "escalates (wrong), and a vip ticket with wait < 30 goes to priority (wrong). "
            "The defect "
            "appears only through the interaction of the wait rule and the vip exception."
        ),
        "evidence": [
            "requirements.txt Exception to R3 with vip",
            "spec.json S3 resolves before S4 (general vip rule)",
            "workflow.json b3 resolves before b4",
        ],
        "scenarios": [
            "interacting exceptions",
            "wrong precedence",
            "multiple individually plausible rules",
        ],
    },
    {
        "bundle_id": "E05_unsupported_language",
        "path": "evaluation/bundles/E05_unsupported_language",
        "status": "INCOMPLETE",
        "classes": ["UNSUPPORTED_LANGUAGE"],
        "reasoning": (
            "The requirements add the sentence 'Refunds must be routed to the finance team by a "
            "senior reviewer', which is outside the supported rule grammar and has no explicit "
            "condition. The supported four rules still agree with the artifacts, but the extra "
            "normative-looking prose cannot be verified, so the review abstains rather than PASS."
        ),
        "evidence": [
            "requirements.txt final sentence outside the rule grammar",
            "spec.json and workflow.json cover only the four supported rules",
        ],
        "scenarios": ["unsupported requirement language"],
    },
    {
        "bundle_id": "E06_unsupported_python",
        "path": "evaluation/bundles/E06_unsupported_python",
        "status": "INCOMPLETE",
        "classes": ["UNSUPPORTED_CODE"],
        "reasoning": (
            "The linked router contains a for loop, which is outside the restricted statement set "
            "the verifier is allowed to execute. The spec and workflow pass, "
            "but the Python behavior "
            "cannot be established, so the review reports a code coverage gap instead of PASS."
        ),
        "evidence": [
            "router.py contains a for statement",
            "spec.json and workflow.json match the requirements",
        ],
        "scenarios": ["unsupported Python syntax"],
    },
    {
        "bundle_id": "E07_missing_evidence",
        "path": "evaluation/bundles/E07_missing_evidence",
        "status": "INCOMPLETE",
        "classes": ["PROVENANCE_FAILURE"],
        "reasoning": (
            "The manifest declares a spec sha256 that does not match spec.json. The review target "
            "still names SPEC@1.0, but the artifact cannot be loaded as evidence, so specification "
            "coverage is unavailable and the result is INCOMPLETE rather than a silent skip."
        ),
        "evidence": [
            "bundle.json spec sha256 is all zeros",
            "spec.json bytes hash differently",
        ],
        "scenarios": ["missing evidence", "tampered artifact"],
    },
    {
        "bundle_id": "E08_unusual_formulation",
        "path": "evaluation/bundles/E08_unusual_formulation",
        "status": "PASS",
        "classes": [],
        "reasoning": (
            "R3 is written 'not waiting < 30 minutes'. Over the declared integer wait domain this "
            "is equivalent to wait >= 30, "
            "and when wait is absent both formulations are UNKNOWN, so "
            "the rule does not fire and R4 handles the ticket. The spec and workflow use the "
            "equivalent >= 30 form, so the review passes within the declared domain and "
            "missing-value semantics."
        ),
        "evidence": [
            "requirements.txt R3 uses not waiting < 30 minutes",
            "dictionary.json aliases waiting -> wait_time",
            "spec.json S3 uses wait_time >= 30 minutes",
        ],
        "scenarios": [
            "valid unusual formulation",
            "missing-value semantics",
            "inclusive versus exclusive boundaries",
        ],
    },
    {
        "bundle_id": "E09_reordered_records",
        "path": "evaluation/bundles/E09_reordered_records",
        "status": "PASS",
        "classes": [],
        "reasoning": (
            "Only serialized record order changed: the spec rules array is reversed while the "
            "priority values stay 1/2/3, and the terminal workflow nodes are listed in reverse. "
            "Execution order is defined by priorities and branch order, not array position, so the "
            "review must pass. This pairs with H03, where priority values actually changed."
        ),
        "evidence": [
            "spec.json rules reversed with priorities unchanged",
            "workflow.json terminal node list reversed",
        ],
        "scenarios": ["metamorphic: only serialized record order changes"],
    },
    {
        "bundle_id": "E10_paraphrased_wording",
        "path": "evaluation/bundles/E10_paraphrased_wording",
        "status": "PASS",
        "classes": [],
        "reasoning": (
            "R3 says 'waiting is at least 30 minutes' instead of 'waiting >= 30 minutes'. The "
            "paraphrase is semantically identical, so the unchanged spec and workflow remain "
            "equivalent. This pairs with E08 and the original wording to show wording invariance."
        ),
        "evidence": [
            "requirements.txt R3 paraphrased",
            "spec.json S3 canonical form",
        ],
        "scenarios": ["metamorphic: supported wording paraphrased"],
    },
]


def main() -> int:
    destination = ROOT / "evaluation" / "cases"
    destination.mkdir(parents=True, exist_ok=True)
    failures = 0
    for case in CASES:
        bundle_path = ROOT / str(case["path"])
        result = oracle.evaluate_bundle(bundle_path)
        expected_status = str(case["status"])
        expected_classes = set(case["classes"])  # type: ignore[arg-type]
        if result.status != expected_status:
            print(
                f"FAIL {case['bundle_id']}: hand label {expected_status} != oracle {result.status}"
            )
            failures += 1
            continue
        missing = expected_classes - set(result.defect_classes)
        if missing:
            print(f"FAIL {case['bundle_id']}: oracle misses {sorted(missing)}")
            failures += 1
            continue
        payload = {
            "bundle_id": case["bundle_id"],
            "suite": "development",
            "bundle_path": case["path"],
            "expected": {
                "status": expected_status,
                "defect_classes": sorted(expected_classes),
                "primary_defect": result.primary_reason,
                "tickets": result.tickets,
            },
            "method": "independent-oracle and manual reasoning",
            "oracle": "evaluation/oracle.py",
            "independent_reasoning": case["reasoning"],
            "source_evidence": case["evidence"],
            "scenarios": case["scenarios"],
            "reviewer_output_used": False,
        }
        target = destination / f"{case['bundle_id']}.json"
        target.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"wrote {target.relative_to(ROOT)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
