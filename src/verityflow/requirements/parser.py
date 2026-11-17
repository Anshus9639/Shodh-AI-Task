from __future__ import annotations

import re
from fractions import Fraction
from typing import Literal, cast

from verityflow.ir.model import (
    And,
    Cmp,
    Const,
    Eq,
    Expr,
    In,
    Missing,
    Ne,
    NotIn,
    NotNode,
    Or,
)
from verityflow.ir.units import UnitError, get_unit, normalize
from verityflow.models.common import TextSpan
from verityflow.models.conditions import to_ir
from verityflow.models.dictionary import DataDictionary, FieldDecl, FieldType
from verityflow.models.report import Severity
from verityflow.requirements.document import (
    IssueCode,
    ParsedRule,
    ParsedRuleKind,
    ParseIssue,
    RequirementDocument,
)
from verityflow.requirements.lexer import Token, TokenizeError, TokenKind, tokenize

_RULE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_.-]*)\.\s*(.*)$")
_Op = Literal["<", "<=", ">", ">=", "==", "!="]
_EXCEPTION_RE = re.compile(
    r"^Exception\s+to\s+([A-Za-z][A-Za-z0-9_.-]*)\s*:\s*(.+)$", re.IGNORECASE
)
_APPROVAL_CLAIM_RE = re.compile(r"(?i)\b(approved|approval by|signed off|authorized by)\b")
_NORMATIVE_RE = re.compile(r"(?i)\b(must|should|route|if|unless|only|never|always)\b")
_TERMINAL_PUNCTUATION_RE = re.compile(r"(?<=[A-Za-z0-9)\]])\s*[.;]\s*$")
_RESERVED = {
    "and",
    "or",
    "not",
    "unless",
    "route",
    "to",
    "all",
    "remaining",
    "tickets",
    "otherwise",
    "inclusive",
    "exclusive",
    "of",
    "one",
    "between",
    "missing",
    "present",
    "true",
    "false",
}
_IS_COMPARATORS: tuple[tuple[tuple[str, ...], _Op], ...] = (
    (("greater", "than", "or", "equal", "to"), ">="),
    (("less", "than", "or", "equal", "to"), "<="),
    (("at", "least"), ">="),
    (("at", "most"), "<="),
    (("more", "than"), ">"),
    (("greater", "than"), ">"),
    (("less", "than"), "<"),
)


class RuleParseError(ValueError):
    def __init__(self, message: str, column: int) -> None:
        super().__init__(message)
        self.column = column


class AmbiguityError(RuleParseError):
    pass


class ConditionTextError(ValueError):
    pass


class _Parser:
    def __init__(
        self,
        tokens: list[Token],
        dictionary: DataDictionary,
        source_text: str,
        line: int,
        path: str,
        issues: list[ParseIssue],
    ) -> None:
        self._tokens = tokens
        self._dictionary = dictionary
        self._source = source_text
        self._line = line
        self._path = path
        self._issues = issues
        self._index = 0
        self._destination_phrases = self._build_destination_phrases()
        self._predicate_phrases = [
            (phrase.split(), to_ir(expression))
            for phrase, expression in dictionary.named_predicates.items()
        ]

    def _build_destination_phrases(self) -> list[tuple[list[str], str]]:
        phrases: list[tuple[list[str], str]] = []
        for destination in self._dictionary.destinations:
            phrases.append((destination.id.split("_"), destination.id))
            if "_" not in destination.id:
                phrases.append((destination.id.split(), destination.id))
            for alias in destination.aliases:
                phrases.append((alias.split(), destination.id))
        return phrases

    def anchor(self) -> TextSpan:
        return TextSpan(
            path=self._path,
            line_start=self._line,
            line_end=self._line,
            col_start=1,
            col_end=len(self._source),
            excerpt=self._source,
        )

    def _issue(self, code: IssueCode, message: str, token: Token, severity: Severity) -> None:
        self._issues.append(
            ParseIssue(
                code=code,
                message=message,
                line=self._line,
                column=token.column,
                severity=severity,
                excerpt=self._source,
            )
        )

    def _peek(self, offset: int = 0) -> Token:
        index = min(self._index + offset, len(self._tokens) - 1)
        return self._tokens[index]

    def _advance(self) -> Token:
        token = self._peek()
        if token.kind is not TokenKind.EOF:
            self._index += 1
        return token

    def _at_end(self) -> bool:
        return self._peek().kind is TokenKind.EOF

    def _word_is(self, token: Token, *words: str) -> bool:
        return token.kind is TokenKind.WORD and token.text.casefold() in words

    def _accept_word(self, *words: str) -> Token | None:
        if self._word_is(self._peek(), *words):
            return self._advance()
        return None

    def _expect_word(self, *words: str) -> Token:
        token = self._accept_word(*words)
        if token is None:
            expected = "/".join(words)
            raise RuleParseError(f"expected {expected!r}", self._peek().column)
        return token

    def _expect_kind(self, kind: TokenKind, description: str) -> Token:
        token = self._peek()
        if token.kind is not kind:
            raise RuleParseError(f"expected {description}", token.column)
        return self._advance()

    def _accept_op(self, text: str) -> bool:
        token = self._peek()
        if token.kind is TokenKind.OP and token.text == text:
            self._advance()
            return True
        return False

    def _expect_op(self, text: str) -> None:
        if not self._accept_op(text):
            raise RuleParseError(f"expected {text!r}", self._peek().column)

    def _words_ahead(self, words: list[str]) -> bool:
        for offset, word in enumerate(words):
            token = self._peek(offset)
            if token.kind is not TokenKind.WORD or token.text.casefold() != word.casefold():
                return False
        return True

    def _accept_phrase(self, words: tuple[str, ...]) -> bool:
        if self._words_ahead(list(words)):
            self._index += len(words)
            return True
        return False

    def _match_named_predicate(self) -> Expr | None:
        best_length = -1
        best_expr: Expr | None = None
        for words, expression in self._predicate_phrases:
            if len(words) > best_length and self._words_ahead(words):
                best_length = len(words)
                best_expr = expression
        if best_expr is None:
            return None
        self._index += best_length
        return best_expr

    def _match_destination(self) -> str:
        best_length = -1
        best_target: str | None = None
        for words, target in self._destination_phrases:
            if len(words) > best_length and self._words_ahead(words):
                best_length = len(words)
                best_target = target
        if best_target is None:
            raise AmbiguityError(
                f"unknown destination near {self._peek().text!r}", self._peek().column
            )
        self._index += best_length
        return best_target

    def _resolve_field(self) -> FieldDecl:
        token = self._peek()
        if token.kind is not TokenKind.WORD:
            raise RuleParseError("expected a field name", token.column)
        self._advance()
        name = self._dictionary.resolve_field(token.text)
        if name is None:
            raise AmbiguityError(f"unknown field {token.text!r}", token.column)
        field = self._dictionary.field(name)
        if field is None:
            raise AmbiguityError(f"unknown field {name!r}", token.column)
        return field

    def parse_condition(self) -> Expr:
        items = [self._parse_factor()]
        connectors: set[str] = set()
        while True:
            token = self._peek()
            if self._word_is(token, "and"):
                connectors.add("and")
            elif self._word_is(token, "or"):
                connectors.add("or")
            else:
                break
            if len(connectors) > 1:
                raise AmbiguityError(
                    "mixed 'and'/'or' without parentheses is ambiguous",
                    token.column,
                )
            self._advance()
            items.append(self._parse_factor())
        if len(items) == 1:
            return items[0]
        if "and" in connectors:
            return And(items=tuple(items))
        return Or(items=tuple(items))

    def _parse_factor(self) -> Expr:
        if self._accept_word("not") is not None:
            return NotNode(item=self._parse_factor())
        token = self._peek()
        if token.kind is TokenKind.LPAREN:
            self._advance()
            inner = self.parse_condition()
            self._expect_kind(TokenKind.RPAREN, "')'")
            return inner
        predicate = self._match_named_predicate()
        if predicate is not None:
            return predicate
        if self._accept_word("missing") is not None:
            return Missing(field=self._resolve_field().name)
        if self._accept_word("present") is not None:
            return NotNode(item=Missing(field=self._resolve_field().name))
        if token.kind is TokenKind.WORD and self._dictionary.resolve_field(token.text) is not None:
            field = self._resolve_field()
            return self._parse_after_field(field)
        if token.kind is TokenKind.WORD:
            raise AmbiguityError(f"unknown field or predicate {token.text!r}", token.column)
        raise RuleParseError("expected a condition", token.column)

    def _parse_after_field(self, field: FieldDecl) -> Expr:
        if self._accept_word("is") is not None:
            if self._accept_word("missing") is not None:
                return Missing(field=field.name)
            if self._accept_word("present") is not None:
                return NotNode(item=Missing(field=field.name))
            if self._accept_word("not") is not None:
                if self._accept_word("missing") is not None:
                    return NotNode(item=Missing(field=field.name))
                if self._accept_phrase(("one", "of")):
                    return self._parse_membership(field, negated=True)
                return self._parse_equality(field, negated=True)
            if self._accept_phrase(("one", "of")):
                return self._parse_membership(field, negated=False)
            if self._accept_word("between") is not None:
                return self._parse_between(field)
            comparator = self._parse_is_comparator()
            if comparator is not None:
                return self._parse_quantity_cmp(field, comparator)
            return self._parse_equality(field, negated=False)
        if self._accept_word("exceeds") is not None:
            return self._parse_quantity_cmp(field, ">")
        token = self._peek()
        if token.kind is TokenKind.OP and token.text in {">=", ">", "<=", "<", "==", "!=", "="}:
            self._advance()
            operator = cast(_Op, "==" if token.text == "=" else token.text)
            return self._parse_quantity_cmp(field, operator)
        if field.type is FieldType.BOOLEAN and (
            token.kind in {TokenKind.EOF, TokenKind.COMMA, TokenKind.RPAREN}
            or (token.kind is TokenKind.OP and token.text == "->")
            or self._word_is(token, "and", "or", "unless")
        ):
            return Eq(field=field.name, value=True)
        raise RuleParseError(f"expected a comparison after field {field.name!r}", token.column)

    def _parse_is_comparator(self) -> _Op | None:
        for words, operator in _IS_COMPARATORS:
            if self._accept_phrase(words):
                return operator
        return None

    def _parse_quantity(self, field: FieldDecl) -> tuple[Fraction, str | None]:
        token = self._expect_kind(TokenKind.NUMBER, "a number")
        try:
            value = Fraction(token.text)
        except (ValueError, ZeroDivisionError) as exc:
            raise AmbiguityError(f"invalid number {token.text!r}", token.column) from exc
        unit: str | None = None
        if self._peek().kind is TokenKind.WORD and get_unit(self._peek().text) is not None:
            unit = self._advance().text
        if field.unit is None:
            if unit is not None:
                raise AmbiguityError(
                    f"field {field.name!r} declares no unit but {unit!r} was given",
                    token.column,
                )
            return value, None
        if unit is None:
            raise AmbiguityError(
                f"quantity for {field.name!r} requires a unit (declared {field.unit!r})",
                token.column,
            )
        try:
            return normalize(value, unit, field.unit), field.unit
        except UnitError as exc:
            raise AmbiguityError(str(exc), token.column) from exc

    def _parse_quantity_cmp(self, field: FieldDecl, operator: _Op) -> Expr:
        if not field.is_numeric:
            raise AmbiguityError(
                f"field {field.name!r} is not numeric; use equality or membership",
                self._peek().column,
            )
        value, unit = self._parse_quantity(field)
        return Cmp(field=field.name, op=operator, value=value, unit=unit)

    def _parse_equality(self, field: FieldDecl, negated: bool) -> Expr:
        if field.is_numeric:
            quantity, unit = self._parse_quantity(field)
            return Cmp(
                field=field.name,
                op="!=" if negated else "==",
                value=quantity,
                unit=unit,
            )
        literal = self._parse_literal(field)
        if negated:
            return Ne(field=field.name, value=literal)
        return Eq(field=field.name, value=literal)

    def _parse_literal(self, field: FieldDecl) -> str | bool:
        token = self._peek()
        value: str | bool
        if token.kind is TokenKind.STRING:
            self._advance()
            value = token.text
        elif token.kind is TokenKind.WORD and token.text.casefold() in {"true", "false"}:
            self._advance()
            value = token.text.casefold() == "true"
        elif token.kind is TokenKind.WORD and token.text.casefold() not in _RESERVED:
            self._advance()
            value = token.text
        else:
            raise RuleParseError("expected a literal value", token.column)
        if field.type.value == "boolean" and not isinstance(value, bool):
            raise AmbiguityError(
                f"field {field.name!r} is boolean; expected true/false", token.column
            )
        if field.type.value == "string" and not isinstance(value, str):
            raise AmbiguityError(
                f"field {field.name!r} is textual; expected a string", token.column
            )
        return value

    def _parse_membership(self, field: FieldDecl, negated: bool) -> Expr:
        if field.is_numeric:
            raise AmbiguityError(
                f"membership is not supported for numeric field {field.name!r}",
                self._peek().column,
            )
        self._expect_kind(TokenKind.LPAREN, "'('")
        values: list[str | bool] = []
        while True:
            values.append(self._parse_literal(field))
            if self._peek().kind is TokenKind.COMMA:
                self._advance()
                continue
            break
        self._expect_kind(TokenKind.RPAREN, "')'")
        if negated:
            return NotIn(field=field.name, values=frozenset(values))
        return In(field=field.name, values=frozenset(values))

    def _parse_between(self, field: FieldDecl) -> Expr:
        if not field.is_numeric:
            raise AmbiguityError(
                f"'between' is not supported for field {field.name!r}",
                self._peek().column,
            )
        low, low_unit = self._parse_quantity(field)
        self._expect_word("and")
        high, high_unit = self._parse_quantity(field)
        if low > high:
            raise AmbiguityError(
                f"empty interval: {low} is greater than {high}", self._peek().column
            )
        inclusive_token = self._peek()
        low_op: _Op
        high_op: _Op
        if self._accept_word("inclusive") is not None:
            low_op, high_op = ">=", "<="
        elif self._accept_word("exclusive") is not None:
            low_op, high_op = ">", "<"
        else:
            raise AmbiguityError(
                "'between' requires explicit 'inclusive' or 'exclusive'",
                inclusive_token.column,
            )
        return And(
            items=(
                Cmp(field=field.name, op=low_op, value=low, unit=low_unit),
                Cmp(field=field.name, op=high_op, value=high, unit=high_unit),
            )
        )

    def _parse_edge(self) -> tuple[Expr, str]:
        if self._accept_word("all") is not None:
            self._expect_word("remaining")
            self._expect_word("tickets")
            self._expect_op("->")
            return Const(value=True), self._match_destination()
        if self._word_is(self._peek(), "route"):
            raise RuleParseError("'route to' needs a preceding condition", self._peek().column)
        if self._accept_word("if") is not None:
            condition = self.parse_condition()
            self._expect_kind(TokenKind.COMMA, "',' after an 'if' condition")
            self._expect_word("route")
            self._expect_word("to")
            return condition, self._match_destination()
        condition = self.parse_condition()
        self._expect_op("->")
        return condition, self._match_destination()

    def parse_body(self, *, allow_otherwise: bool = True) -> tuple[Expr, str, bool]:
        had_otherwise = False
        if allow_otherwise and self._accept_word("otherwise") is not None:
            had_otherwise = True
            if self._peek().kind is TokenKind.COMMA:
                self._advance()
        if had_otherwise and self._accept_word("route") is not None:
            self._expect_word("to")
            condition: Expr = Const(value=True)
            destination = self._match_destination()
        else:
            condition, destination = self._parse_edge()
        if self._peek().kind is TokenKind.COMMA:
            self._advance()
            self._expect_word("unless")
            unless = self.parse_condition()
            condition = _and(condition, NotNode(item=unless))
        if not self._at_end():
            raise RuleParseError(f"unexpected token {self._peek().text!r}", self._peek().column)
        return condition, destination, had_otherwise


def _and(first: Expr, second: Expr) -> Expr:
    if isinstance(first, Const) and first.value:
        return second
    if isinstance(second, Const) and second.value:
        return first
    return And(items=(first, second))


def _parse_body_text(
    body: str,
    body_start: int,
    line: str,
    line_number: int,
    path: str,
    dictionary: DataDictionary,
    issues: list[ParseIssue],
    *,
    allow_otherwise: bool,
) -> tuple[Expr, str, bool]:
    tokens = tokenize(body, base_column=body_start + 1)
    parser = _Parser(tokens, dictionary, line, line_number, path, issues)
    return parser.parse_body(allow_otherwise=allow_otherwise)


def parse_requirements(text: str, path: str, dictionary: DataDictionary) -> RequirementDocument:
    lines = text.splitlines()
    issues: list[ParseIssue] = []
    parsed: list[ParsedRule] = []
    prose_lines: list[int] = []
    exception_counts: dict[str, int] = {}
    blank = 0
    headings = 0

    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r")
        stripped = line.strip()
        if not stripped:
            blank += 1
            continue
        if _APPROVAL_CLAIM_RE.search(stripped):
            issues.append(
                ParseIssue(
                    code=IssueCode.UNTRUSTED_APPROVAL_CLAIM,
                    message="approval-like claim in text is never treated as authority",
                    line=number,
                    column=None,
                    severity=Severity.INFO,
                    excerpt=stripped,
                )
            )
        if stripped.startswith("#"):
            headings += 1
            continue
        sentence = _TERMINAL_PUNCTUATION_RE.sub("", stripped)
        anchor = TextSpan(
            path=path,
            line_start=number,
            line_end=number,
            col_start=1,
            col_end=len(line),
            excerpt=line,
        )
        exception = _EXCEPTION_RE.match(sentence)
        if exception is not None:
            target = exception.group(1)
            body = exception.group(2)
            try:
                condition, destination, _ = _parse_body_text(
                    body,
                    stripped.index(body),
                    line,
                    number,
                    path,
                    dictionary,
                    issues,
                    allow_otherwise=False,
                )
            except RuleParseError as exc:
                issues.append(
                    ParseIssue(
                        code=IssueCode.AMBIGUITY
                        if isinstance(exc, AmbiguityError)
                        else IssueCode.UNSUPPORTED_LANGUAGE,
                        message=str(exc),
                        line=number,
                        column=exc.column,
                        severity=Severity.COVERAGE_GAP,
                        excerpt=stripped,
                    )
                )
                continue
            parsed.append(
                ParsedRule(
                    rule_ref=_exception_ref(target, exception_counts),
                    kind=ParsedRuleKind.EXCEPTION,
                    anchor=anchor,
                    condition=condition,
                    destination=destination,
                    exception_of=target,
                    source_text=stripped,
                )
            )
            continue
        rule = _RULE_RE.match(sentence)
        if rule is not None:
            rule_ref = rule.group(1)
            body = rule.group(2)
            if not body.strip():
                issues.append(
                    ParseIssue(
                        code=IssueCode.UNSUPPORTED_LANGUAGE,
                        message="rule line has no body",
                        line=number,
                        column=1,
                        severity=Severity.COVERAGE_GAP,
                        excerpt=stripped,
                    )
                )
                continue
            try:
                condition, destination, had_otherwise = _parse_body_text(
                    body,
                    rule.start(2),
                    line,
                    number,
                    path,
                    dictionary,
                    issues,
                    allow_otherwise=True,
                )
            except RuleParseError as exc:
                issues.append(
                    ParseIssue(
                        code=IssueCode.AMBIGUITY
                        if isinstance(exc, AmbiguityError)
                        else IssueCode.UNSUPPORTED_LANGUAGE,
                        message=str(exc),
                        line=number,
                        column=exc.column,
                        severity=Severity.COVERAGE_GAP,
                        excerpt=stripped,
                    )
                )
                continue
            kind = ParsedRuleKind.CATCH_ALL if _is_catch_all(condition) else ParsedRuleKind.RULE
            parsed.append(
                ParsedRule(
                    rule_ref=rule_ref,
                    kind=kind,
                    anchor=anchor,
                    condition=condition,
                    destination=destination,
                    had_otherwise=had_otherwise,
                    source_text=stripped,
                )
            )
            continue
        prose_lines.append(number)
        if _NORMATIVE_RE.search(stripped):
            issues.append(
                ParseIssue(
                    code=IssueCode.UNSUPPORTED_LANGUAGE,
                    message="normative-looking prose is outside the supported rule grammar",
                    line=number,
                    column=None,
                    severity=Severity.COVERAGE_GAP,
                    excerpt=stripped,
                )
            )

    rules, issues = _finalize_rules(parsed, issues, path)
    return RequirementDocument(
        path=path,
        rules=tuple(rules),
        issues=tuple(issues),
        line_count=len(lines),
        blank_lines=blank,
        heading_lines=headings,
        prose_lines=tuple(prose_lines),
    )


def _exception_ref(target: str, counts: dict[str, int]) -> str:
    count = counts.get(target, 0) + 1
    counts[target] = count
    if count == 1:
        return f"{target}.exception"
    return f"{target}.exception#{count}"


def _is_catch_all(condition: Expr) -> bool:
    return isinstance(condition, Const) and condition.value


def _finalize_rules(
    parsed: list[ParsedRule], issues: list[ParseIssue], path: str
) -> tuple[list[ParsedRule], list[ParseIssue]]:
    seen: set[str] = set()
    valid_normal: list[ParsedRule] = []
    duplicates: set[int] = set()
    for index, rule in enumerate(parsed):
        if rule.kind is ParsedRuleKind.EXCEPTION:
            continue
        if rule.rule_ref in seen:
            duplicates.add(index)
            issues.append(
                ParseIssue(
                    code=IssueCode.AMBIGUITY,
                    message=f"duplicate rule id {rule.rule_ref!r}",
                    line=rule.anchor.line_start,
                    column=rule.anchor.col_start,
                    severity=Severity.COVERAGE_GAP,
                    excerpt=rule.source_text,
                )
            )
            continue
        seen.add(rule.rule_ref)
        valid_normal.append(rule)

    catch_alls = [rule for rule in valid_normal if rule.kind is ParsedRuleKind.CATCH_ALL]
    if len(catch_alls) > 1:
        for rule in catch_alls[1:]:
            issues.append(
                ParseIssue(
                    code=IssueCode.AMBIGUITY,
                    message="more than one catch-all rule",
                    line=rule.anchor.line_start,
                    column=rule.anchor.col_start,
                    severity=Severity.COVERAGE_GAP,
                    excerpt=rule.source_text,
                )
            )
    if catch_alls:
        catch_all = catch_alls[0]
        later = [
            rule for rule in valid_normal if rule.anchor.line_start > catch_all.anchor.line_start
        ]
        if later:
            issues.append(
                ParseIssue(
                    code=IssueCode.AMBIGUITY,
                    message="rules after a catch-all rule are unreachable",
                    line=later[0].anchor.line_start,
                    column=later[0].anchor.col_start,
                    severity=Severity.COVERAGE_GAP,
                    excerpt=later[0].source_text,
                )
            )

    final: list[ParsedRule] = []
    for index, rule in enumerate(parsed):
        if index in duplicates:
            continue
        if rule.kind is ParsedRuleKind.EXCEPTION:
            if rule.exception_of not in seen:
                issues.append(
                    ParseIssue(
                        code=IssueCode.AMBIGUITY,
                        message=f"exception targets unknown rule {rule.exception_of!r}",
                        line=rule.anchor.line_start,
                        column=rule.anchor.col_start,
                        severity=Severity.COVERAGE_GAP,
                        excerpt=rule.source_text,
                    )
                )
                continue
        final.append(rule)
    return final, issues


def parse_condition_text(text: str, dictionary: DataDictionary) -> Expr:
    issues: list[ParseIssue] = []
    try:
        tokens = tokenize(text)
    except TokenizeError as exc:
        raise ConditionTextError(str(exc)) from exc
    parser = _Parser(tokens, dictionary, text, 1, "<inline>", issues)
    try:
        expression = parser.parse_condition()
    except RuleParseError as exc:
        raise ConditionTextError(str(exc)) from exc
    if not parser._at_end():
        raise ConditionTextError(f"unexpected token {parser._peek().text!r}")
    errors = [issue for issue in issues if issue.severity is Severity.COVERAGE_GAP]
    if errors:
        raise ConditionTextError(errors[0].message)
    return expression


__all__ = [
    "AmbiguityError",
    "ConditionTextError",
    "RuleParseError",
    "TokenizeError",
    "parse_condition_text",
    "parse_requirements",
]
