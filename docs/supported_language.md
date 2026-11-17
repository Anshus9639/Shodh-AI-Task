# Supported requirement language

This document is the authoritative description of the Stage 1 requirement grammar. Anything outside it is reported as `UNSUPPORTED_LANGUAGE` or `AMBIGUITY`; it is never guessed and never silently ignored.

## Document shape

- UTF-8 text, one rule per line. Rules may not span lines.
- Blank lines and lines starting with `#` are ignored (`blank_lines`, `heading_lines` in the parse result).
- One trailing `.` or `;` is removed before parsing; it is not part of the condition.
- Every other non-empty line is either a rule, an `Exception to ...` line, or prose. Prose that contains normative words (`must`, `should`, `if`, `route`, `unless`, `only`, `never`, `always`) produces an `UNSUPPORTED_LANGUAGE` coverage gap on that line. Prose without normative words is recorded as `prose_lines` and does not change the policy.
- Approval-like claims ("approved by ...", "signed off ...") emit `UNTRUSTED_APPROVAL_CLAIM` at INFO. Text can never grant authority; only structured approval records bound to a change hash can.

## Rule lines

```
<ref>. <body>
```

`<ref>` matches `[A-Za-z][A-Za-z0-9_.-]*`. Duplicate refs, a missing catch-all, rules after a catch-all, and multiple catch-alls are reported as `AMBIGUITY`.

### The four forms used by the starter document

```
R1. Missing category -> manual review.
R2. Otherwise, urgent ticket -> priority queue.
R3. Otherwise, waiting >= 30 minutes -> escalation queue.
R4. All remaining tickets -> normal queue.
```

- `Missing <field>` is true only when the field is absent or null.
- `Otherwise,` is ordering sugar. It adds no implicit negation and is not stored. Precedence is physical line order with first-`TRUE`-match semantics (see below).
- `urgent ticket` is a named predicate declared in the data dictionary; it expands to an `Eq` condition on `priority`.
- `waiting` is a field alias for `wait_time`; `30 minutes` is normalized against the field's declared unit.
- `All remaining tickets -> <destination>` is the catch-all. It must be the last rule.
- Destination phrases (`manual review`, `priority queue`, `escalation queue`, `normal queue`) are resolved through declared destination ids and aliases.

### Other accepted bodies

```
R5. If vip, route to priority queue.
R6. waiting > 10 minutes -> escalation queue.
R7. category is "billing" -> billing queue.
R8. category is not one of ("billing", "fraud") -> normal queue.
R9. amount between 10 usd and 20 usd inclusive -> review queue.
R10. vip -> priority queue.
```

- `If <condition>, route to <destination>` and `<condition> -> <destination>` are equivalent.
- Numeric comparisons: `>`, `>=`, `<`, `<=`, `==`, `=`, `!=`, `exceeds`, `greater than`, `less than`, `at least`, `at most`, `greater than or equal to`, `less than or equal to`.
- Membership: `<field> is one of (<literal>, ...)` and `<field> is not one of (...)`. The literal list is parenthesized; membership is only defined for non-numeric fields.
- Equality: `<field> is <literal>` and `<field> is not <literal>`.
- Quantity ranges: `<field> between <n> <unit> and <m> <unit> inclusive|exclusive`. The bound kind is mandatory; bare `between` is `AMBIGUITY`.
- Presence: `<field> is missing`, `<field> is present`, `<field> is not missing`.
- Boolean fields may be used bare: `vip -> ...`. `not vip`, `vip and waiting >= 30 minutes`, etc. are accepted.
- Boolean connectives: `and`, `or`, `not`, and parentheses. Mixing `and` and `or` without parentheses is `AMBIGUITY`; no precedence is invented.

## Exceptions and `unless`

```
Exception to R3: if vip, route to priority queue.
R3. Otherwise, waiting >= 30 minutes -> escalation queue, unless vip.
```

- `Exception to R: <body>` is equivalent to `(R.condition ∧ C) → D` and is placed immediately before `R` in the ordered list. It can only narrow cases `R` would already have covered.
- `, unless C` is equivalent to `R.condition ∧ ¬C`.
- An exception whose target does not exist is `AMBIGUITY` and is dropped. Duplicate exception targets receive distinct refs (`R3.exception`, `R3.exception#2`).

## Ordering semantics

Rules are an ordered first-match decision list. The first entry whose raw condition evaluates to TRUE fires. The compiled entry guard is:

```
guard_i = cond_i ∧ ⋀_{j<i} NotTrue(cond_j)
```

`NotTrue(x)` is an internal IR node that is TRUE unless `x` is TRUE (so UNKNOWN does not count as "already fired"). It is never emitted by the source grammar. This guard is exact: the earliest TRUE raw condition is never blocked by an earlier entry, and later entries cannot fire when an earlier one did. Constants are folded (`NotTrue(True) → False`, `NotTrue(False) → True`).

A catch-all has condition `Const(True)`, and its guard is the conjunction of `NotTrue` over all earlier conditions. It carries no information about earlier entries beyond "nothing fired".

Specification artifacts order by the explicit integer `priority` field; array position is ignored. Duplicate priorities make the ordered list undetermined and produce `AMBIGUITY` with no extracted elements. Workflow artifacts order by graph structure and ordered branch lists; an else-branch is the negation of the preceding branch conditions.

## Linked router subset (Stage 2)

A Python artifact is reviewed only if it parses to the following subset. It is parsed with `ast` and interpreted; it is never imported, compiled or executed.

- Module docstring, `from __future__ import annotations`, and `from typing import ...` / `import typing` statements.
- Module-level literal assignments (used as named constants).
- Exactly one function, named by `CodeLink.entrypoint` (default `route`), taking one parameter (default `ticket`).
- Statements: docstring, `if`/`elif`/`else`, `return`, single-name assignment, `pass`.
- Expressions: literals; names bound to unambiguous literals; `ticket["field"]`; `ticket.get("field")`; `"field" in ticket` / `"field" not in ticket`; one comparison (`==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`) between supported operands; `and`/`or`/`not`; literal tuples/lists/sets.
- Returned values must be destination strings (mapped through `CodeLink.destination_map`) or `None`. `None` means "no match"; falling off the end means "fell through".

Anything else — imports beyond the above, calls, attribute access, subscripts other than `ticket[...]`, loops, `try`, `raise`, classes, decorators, lambdas, comprehensions, multiple functions, `global`/`nonlocal` — is rejected statically as `UNSUPPORTED_CODE` (or `STRUCTURAL` for syntax errors) and the affected region is unreviewed. Budgets (`interpreter_max_steps`, `interpreter_max_ast_nodes`, `interpreter_max_depth`) bound evaluation; exceeding them yields `STEP_LIMIT`, which is also unreviewed, not a defect.

## External artifact conditions

JSON specification rules (`spec.json`), workflow branches (`workflow.json`) and dictionary named predicates accept the **external** condition union:

```
cmp | eq | ne | in | not_in | missing | and | or | not | const
```

`not_true` is not part of that union. Externally supplied artifacts that submit it fail Pydantic validation during ingestion (`STRUCTURAL` coverage gap) and are rejected by the exported schemas (`spec`, `spec_rule`, `workflow`, `workflow_branch`, `data_dictionary`). The internal `NotTrue` guard described above is generated only by the compiler and the workflow extractor; external input cannot create it. Conversion at the boundary is `models.conditions.to_ir`, which re-keys `and`/`or`/`not` composites and leaves leaves unchanged.

## Units

| Family | Units | Canonical |
| --- | --- | --- |
| duration | second(s), sec, minute(s), min, hour(s), hr, day(s) | seconds |
| currency | cent(s), usd, dollar(s) | cents |

Values are exact `Fraction`s. A unit may be written only when the field declares a unit, and it must belong to the same family. Mixed-family or unknown units are `AMBIGUITY`. Canonicalization is applied before any comparison or cross-artifact correspondence.

## Evidence

Every parsed rule keeps a `TextSpan` anchor (path, line, columns, excerpt) pointing at the source line, plus the normalized `source_text`. Anchors are checked against the loaded artifact text with `LineIndex.check_span`; a mismatch is a provenance failure, not a warning.
