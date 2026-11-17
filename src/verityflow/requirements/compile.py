from __future__ import annotations

from verityflow.ir.model import And, Const, Expr
from verityflow.ir.policy import Policy, Rule
from verityflow.models.common import ArtifactRef, TextSpan
from verityflow.models.report import (
    Finding,
    FindingCategory,
    Severity,
    make_finding,
)
from verityflow.requirements.document import (
    IssueCode,
    ParsedRule,
    ParsedRuleKind,
    ParseIssue,
    RequirementDocument,
)

_ISSUE_CATEGORIES = {
    IssueCode.AMBIGUITY: FindingCategory.AMBIGUITY,
    IssueCode.UNSUPPORTED_LANGUAGE: FindingCategory.UNSUPPORTED_LANGUAGE,
    IssueCode.UNTRUSTED_APPROVAL_CLAIM: FindingCategory.UNTRUSTED_APPROVAL_CLAIM,
}


def compile_document(
    document: RequirementDocument,
    *,
    candidate_id: str = "base",
    artifact: ArtifactRef | None = None,
) -> tuple[Policy, tuple[Finding, ...]]:
    """Compile a parsed requirements document into an ordered first-match policy.

    Exceptions are inserted immediately before the rule they qualify so that the
    document order, read as an ordered decision list, is preserved. Nothing is
    silently discarded: every exclusion produces a finding.
    """
    findings: list[Finding] = []
    artifact_id = artifact.artifact_id if artifact is not None else None
    artifact_version = artifact.version if artifact is not None else None

    def report(
        category: FindingCategory,
        severity: Severity,
        message: str,
        *,
        anchor: TextSpan | None = None,
        rule_ref: str | None = None,
    ) -> None:
        findings.append(
            make_finding(
                category,
                severity,
                message,
                rule_ref=rule_ref,
                artifact_id=artifact_id,
                artifact_version=artifact_version,
                location=anchor,
            )
        )

    for issue in document.issues:
        report(
            _ISSUE_CATEGORIES[issue.code],
            issue.severity,
            issue.message,
            anchor=_issue_span(document.path, issue),
        )

    normal: list[ParsedRule] = []
    seen: set[str] = set()
    catch_all_seen = False
    for rule in document.rules:
        if rule.kind is ParsedRuleKind.EXCEPTION:
            continue
        if rule.rule_ref in seen:
            report(
                FindingCategory.AMBIGUITY,
                Severity.COVERAGE_GAP,
                f"duplicate rule id {rule.rule_ref!r}; only the first definition is used",
                anchor=rule.anchor,
                rule_ref=rule.rule_ref,
            )
            continue
        if rule.kind is ParsedRuleKind.CATCH_ALL:
            if catch_all_seen:
                report(
                    FindingCategory.AMBIGUITY,
                    Severity.COVERAGE_GAP,
                    f"additional catch-all rule {rule.rule_ref!r} is unreachable; "
                    "ordered first-match semantics keep only the first",
                    anchor=rule.anchor,
                    rule_ref=rule.rule_ref,
                )
                continue
            catch_all_seen = True
        seen.add(rule.rule_ref)
        normal.append(rule)

    exceptions: dict[str, list[ParsedRule]] = {}
    excluded_exceptions: list[ParsedRule] = []
    for rule in document.rules:
        if rule.kind is not ParsedRuleKind.EXCEPTION:
            continue
        target = rule.exception_of or ""
        if target not in seen:
            report(
                FindingCategory.AMBIGUITY,
                Severity.COVERAGE_GAP,
                f"exception {rule.rule_ref!r} targets unknown or excluded rule {target!r}",
                anchor=rule.anchor,
                rule_ref=rule.rule_ref,
            )
            excluded_exceptions.append(rule)
            continue
        exceptions.setdefault(target, []).append(rule)

    if not normal:
        report(
            FindingCategory.STRUCTURAL,
            Severity.COVERAGE_GAP,
            "requirements document defines no rules in the supported grammar",
            anchor=None,
        )

    catch_all = next((rule for rule in normal if rule.kind is ParsedRuleKind.CATCH_ALL), None)
    if catch_all is None and normal:
        report(
            FindingCategory.STRUCTURAL,
            Severity.WARNING,
            "policy has no catch-all rule; ticket states matched by no rule have no "
            "declared destination and are outside requirement coverage",
            anchor=normal[0].anchor,
        )
    if catch_all is not None:
        later = [rule for rule in normal if rule.anchor.line_start > catch_all.anchor.line_start]
        if later:
            report(
                FindingCategory.PRECEDENCE,
                Severity.COVERAGE_GAP,
                f"rule {later[0].rule_ref!r} follows the catch-all rule "
                f"{catch_all.rule_ref!r} and is unreachable under ordered first-match semantics",
                anchor=later[0].anchor,
                rule_ref=later[0].rule_ref,
            )

    ordered: list[ParsedRule] = []
    emitted_exceptions: set[str] = set()
    for rule in normal:
        for exception in exceptions.get(rule.rule_ref, ()):
            if exception.rule_ref in seen:
                report(
                    FindingCategory.AMBIGUITY,
                    Severity.COVERAGE_GAP,
                    f"exception ref {exception.rule_ref!r} collides with a declared rule id",
                    anchor=exception.anchor,
                    rule_ref=exception.rule_ref,
                )
                continue
            ordered.append(
                exception.model_copy(update={"condition": _exception_condition(rule, exception)})
            )
            emitted_exceptions.add(exception.rule_ref)
        ordered.append(rule)
    for exception in excluded_exceptions:
        emitted_exceptions.add(exception.rule_ref)
    leftover = [
        exception
        for group in exceptions.values()
        for exception in group
        if exception.rule_ref not in emitted_exceptions
    ]
    for exception in leftover:
        report(
            FindingCategory.AMBIGUITY,
            Severity.COVERAGE_GAP,
            f"exception {exception.rule_ref!r} could not be attached to its target",
            anchor=exception.anchor,
            rule_ref=exception.rule_ref,
        )

    rules = tuple(
        Rule(
            rule_ref=item.rule_ref,
            anchor=item.anchor,
            condition=item.condition,
            destination=item.destination,
            precedence=precedence,
            exception_of=item.exception_of,
            origin="base",
            source_text=item.source_text,
        )
        for precedence, item in enumerate(ordered)
    )
    policy = Policy(candidate_id=candidate_id, rules=rules)
    return policy, _dedupe(findings)


def _exception_condition(target: ParsedRule, exception: ParsedRule) -> Expr:
    if isinstance(target.condition, Const) and target.condition.value:
        return exception.condition
    return And(items=(target.condition, exception.condition))


def _issue_span(path: str, issue: ParseIssue) -> TextSpan:
    return TextSpan(
        path=path,
        line_start=issue.line,
        line_end=issue.line,
        col_start=issue.column,
        col_end=None,
        excerpt=issue.excerpt,
    )


def _dedupe(findings: list[Finding]) -> tuple[Finding, ...]:
    seen: set[str] = set()
    unique: list[Finding] = []
    for finding in findings:
        if finding.finding_id in seen:
            continue
        seen.add(finding.finding_id)
        unique.append(finding)
    return tuple(unique)


__all__ = ["compile_document"]
