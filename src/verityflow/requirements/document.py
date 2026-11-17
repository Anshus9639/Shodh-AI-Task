from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from verityflow.ir.model import Expr
from verityflow.models.common import FrozenModel, TextSpan
from verityflow.models.report import Severity


class IssueCode(StrEnum):
    AMBIGUITY = "AMBIGUITY"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    UNTRUSTED_APPROVAL_CLAIM = "UNTRUSTED_APPROVAL_CLAIM"


class ParseIssue(FrozenModel):
    code: IssueCode
    message: str
    line: int = Field(ge=1)
    column: int | None = Field(default=None, ge=1)
    severity: Severity
    excerpt: str


class ParsedRuleKind(StrEnum):
    RULE = "rule"
    EXCEPTION = "exception"
    CATCH_ALL = "catch_all"


class ParsedRule(FrozenModel):
    rule_ref: str
    kind: ParsedRuleKind
    anchor: TextSpan
    condition: Expr
    destination: str
    exception_of: str | None = None
    had_otherwise: bool = False
    source_text: str


class RequirementDocument(FrozenModel):
    path: str
    rules: tuple[ParsedRule, ...] = ()
    issues: tuple[ParseIssue, ...] = ()
    line_count: int = 0
    blank_lines: int = 0
    heading_lines: int = 0
    prose_lines: tuple[int, ...] = ()

    @property
    def rule_count(self) -> int:
        return len(self.rules)
