# Sealed final evaluation

- Expected: {'PASS': 6, 'CHANGES_REQUIRED': 2, 'INCOMPLETE': 2}
- Actual: {'PASS': 5, 'CHANGES_REQUIRED': 4, 'INCOMPLETE': 1}
- Correct statuses: 8/10
- Sealed manifests unchanged: True
- Manifest hashes: `{'holdout': 'e10de058ceb51586f16c657c75e4fd3b4fd9cbded6f76beda9a4e0c648ba6f41', 'holdout_stage2': 'a76fa4bc27dbd28aba49762720ba5292eabcbddbfeb0fe1362a1c6922ae7c3e4'}`

| Bundle | Expected | Actual | Expected findings | Actual findings | Categories match |
| --- | --- | --- | --- | --- | --- |
| F01_clean | PASS | PASS | - | - | True |
| F02_renamed | PASS | PASS | - | - | True |
| F03_approved_threshold | PASS | CHANGES_REQUIRED | - | THRESHOLD_CHANGED, THRESHOLD_CHANGED | False |
| F04_unapproved_threshold | PASS | PASS | UNAPPROVED_CHANGE | UNAPPROVED_CHANGE | True |
| F05_tampered_approval | INCOMPLETE | INCOMPLETE | INVALID_APPROVAL, UNAPPROVED_CHANGE | INVALID_APPROVAL, UNAPPROVED_CHANGE | True |
| G01_valid_original | PASS | PASS | - | - | True |
| G02_valid_approved_25 | PASS | PASS | - | - | True |
| G03_precedence_defect | CHANGES_REQUIRED | CHANGES_REQUIRED | PRECEDENCE | PRECEDENCE, PRECEDENCE, PRECEDENCE, PRECEDENCE | True |
| G04_code_boundary_defect | CHANGES_REQUIRED | CHANGES_REQUIRED | THRESHOLD_CHANGED | SCRIPT_DISAGREEMENT | False |
| G05_conflicting_approvals | INCOMPLETE | CHANGES_REQUIRED | UNDETERMINED_CONFLICT | UNDETERMINED_CONFLICT, STRUCTURAL, WRONG_DESTINATION | False |

Counterexample checks apply to the frozen Stage 2 defect labels (G03, G04).
Route expectations for valid bundles are not separately graded here; the
behavioural equality comparisons and status cover them, and this scope
limitation is recorded in the engineering note.
