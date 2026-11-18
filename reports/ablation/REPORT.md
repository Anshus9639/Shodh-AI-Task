# Controlled ablation: numeric domain partitioning disabled

Only `domain_partitioning.enabled` differs (`configs/no_domain_partitioning.toml`).
Bundles, approvals, model mode (`off`), labels and the environment are unchanged.

| Metric | Enabled | Disabled |
| --- | --- | --- |
| Overall exact status | 27/27 | 10/27 |
| Correct CHANGES_REQUIRED | 11/11 | 4/11 |
| Correct PASS | 10/10 | 0/10 |
| False PASS on defective | 0/11 | 0/11 |
| False blocks | 0/10 | 0/10 |
| Valid counterexamples | 78/78 | 15/15 |
| Comparison modes | {'exhaustive': 63} | {'unreviewed': 63} |
| Evaluated combinations | 24600 | 315 |
| Runtime (s) | 16.98 | 16.43 |

Status changes: 17 of 27 bundles.

## Why behaviour changes

Disabled mode replaces interval partitioning and boundary witnesses with a small fixed
ticket set and records the remaining space as `unreviewed`. The reviewer cannot claim
exhaustive agreement, so every verdict that depended on numeric cells becomes
`INCOMPLETE`; defects that would only appear at a boundary (for example wait = 30 versus
`> 30`) are never discovered. No false PASS is introduced: the reviewer abstains instead
of asserting success. Statuses that remain `CHANGES_REQUIRED` are structural or approval
defects visible on the fixed tickets.

| Bundle | Enabled | Disabled | Reason |
| --- | --- | --- | --- |
| B01_starter | PASS | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| B03_approved_change | CHANGES_REQUIRED | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| B05_unapproved_change | PASS | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| B07_approved_threshold_25 | CHANGES_REQUIRED | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| B08_unapproved_threshold_25 | PASS | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| B10_router_clean | PASS | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| B12_router_threshold | CHANGES_REQUIRED | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| B13_router_runtime | CHANGES_REQUIRED | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| B14_router_unsupported | CHANGES_REQUIRED | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| E01_valid_approved_25 | PASS | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| E04_interacting_exceptions | CHANGES_REQUIRED | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| E08_unusual_formulation | PASS | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| E09_reordered_records | PASS | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| E10_paraphrased_wording | PASS | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| H01_clean | PASS | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| H02_renamed | PASS | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
| H04_approved_threshold | CHANGES_REQUIRED | INCOMPLETE | numeric comparisons were left unreviewed, so the reviewer reports INCOMPLETE instead of claiming a verified PASS or a verified defect |
