"""Restricted Python router interpreter.

Bundle-supplied Python is parsed with :mod:`ast` and interpreted node by node by
this module. The code is never passed to ``exec``/``eval``/``compile`` and no
attribute access beyond ``ticket.get`` is possible, so untrusted bundle content
cannot run arbitrary code.

Supported subset
----------------
Module level: optional docstring, ``from __future__ import annotations``, typing
imports, literal constants and exactly one ``def route(ticket)``.

Statements: docstring, ``if``/``elif``/``else``, ``return``, a single-target
assignment of a supported expression, ``pass``.

Expressions: literal constants (int/str/bool/None), names bound by module
constants or local assignments, ``ticket["field"]``, ``ticket.get("field")``,
``"field" in ticket``, one comparison per ``Compare``, ``and``/``or``/``not``,
and literal tuples/lists/sets as membership operands.

Everything else is a compile-time UNSUPPORTED result; there are no loops,
helper calls, arithmetic, classes, lambdas or comprehensions. Step, node and
depth budgets are enforced.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, cast

from verityflow.config import Limits
from verityflow.ir.evaluate import CellValue, ValueState
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
    NotTrue,
    Or,
)
from verityflow.ir.units import UnitError, normalize
from verityflow.models.common import ArtifactRef
from verityflow.models.dictionary import DataDictionary, FieldDecl, FieldType
from verityflow.models.manifest import CodeLink, FieldMapEntry
from verityflow.verify.routing import RouteKind, RouteOutcome

_COMPARE_OPS: dict[type[ast.cmpop], str] = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.In: "in",
    ast.NotIn: "not in",
}

_TYPE_NAMES = {
    type(None): "NoneType",
    bool: "bool",
    int: "int",
    float: "float",
    str: "str",
}

_CmpOp = Literal["<", "<=", ">", ">=", "==", "!="]

_IR_OPS: dict[type[ast.cmpop], _CmpOp] = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
}

_FLIPPED: dict[_CmpOp, _CmpOp] = {
    "<": ">",
    "<=": ">=",
    ">": "<",
    ">=": "<=",
    "==": "==",
    "!=": "!=",
}

_UNKNOWN = object()


class RouterCodeError(ValueError):
    """The linked router cannot be reviewed at all."""

    def __init__(self, message: str, *, lineno: int | None = None) -> None:
        super().__init__(message)
        self.lineno = lineno


class RouterSyntaxError(RouterCodeError):
    """The artifact is not valid Python syntax."""


class RouterUnsupportedError(RouterCodeError):
    """The artifact uses constructs outside the supported subset."""


class RouterLimitError(RouterCodeError):
    """The artifact exceeds an interpreter budget."""


class _PyFailure(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class _Unsupported(Exception):
    pass


class _StepLimit(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _Returned:
    value: object


@dataclass(frozen=True, slots=True)
class CodeDomain:
    """Numeric cuts and string literals the router compares against, in field units."""

    cuts: Mapping[str, tuple[Fraction, ...]]
    literals: Mapping[str, tuple[str | bool, ...]]


@dataclass(frozen=True, slots=True)
class CodeRule:
    """One ordered guard of a simple if/return chain, expressed as internal IR.

    Extracted only for diagnosis (threshold/operator/precedence classification).
    Witnesses are always produced by actually routing the ticket space.
    """

    condition: Expr
    destination: str
    line: int
    end_line: int
    col_start: int
    col_end: int
    excerpt: str


@dataclass(frozen=True, slots=True)
class CmpFact:
    """A numeric or equality comparison extracted from the AST.

    Unlike :meth:`CodeProgram.routing_signature`, a fact does not require the
    whole if/return chain to be representable. It is used for evidence and for
    diagnosis when a presence guard makes the complete signature unavailable.
    """

    comparison: Expr
    line_start: int
    line_end: int
    col_start: int
    col_end: int
    excerpt: str


@dataclass(frozen=True, slots=True)
class CodeProgram:
    artifact_ref: str
    path: str
    entrypoint: str
    dictionary: DataDictionary
    destination_map: Mapping[str, str]
    field_map: Mapping[str, FieldMapEntry]
    constants: Mapping[str, object]
    function: ast.FunctionDef
    param: str
    max_steps: int
    max_depth: int
    source: str = ""
    name: str = "code"

    def domain_hints(self) -> CodeDomain:
        return _domain_hints(self)

    def routing_signature(self) -> tuple[CodeRule, ...] | None:
        return _routing_signature(self)

    def comparison_facts(self) -> tuple[CmpFact, ...]:
        return _comparison_facts(self)

    def function_segment(self) -> tuple[int, int, str]:
        start = self.function.lineno
        end = self.function.end_lineno or start
        return start, end, ast.get_source_segment(self.source, self.function) or ""

    def route(self, values: Mapping[str, CellValue]) -> RouteOutcome:
        interpreter = _Interpreter(self, values)
        try:
            returned = interpreter.run()
        except _StepLimit:
            return RouteOutcome(
                RouteKind.STEP_LIMIT,
                detail=f"router exceeded interpreter_max_steps={self.max_steps}",
                trace=interpreter.trace,
            )
        except _Unsupported as exc:
            return RouteOutcome(RouteKind.UNSUPPORTED, detail=str(exc), trace=interpreter.trace)
        except _PyFailure as exc:
            return RouteOutcome(
                RouteKind.ERROR,
                detail=f"{exc.kind}: {exc.message}",
                trace=interpreter.trace,
            )
        if returned is None:
            return RouteOutcome(
                RouteKind.FALL_THROUGH,
                detail="router completed without returning a destination",
                trace=interpreter.trace,
            )
        value = returned.value
        if value is None:
            return RouteOutcome(
                RouteKind.NO_MATCH, detail="router returned None", trace=interpreter.trace
            )
        if not isinstance(value, str):
            return RouteOutcome(
                RouteKind.UNSUPPORTED,
                detail=f"router returned {_type_name(value)}, not a destination string",
                trace=interpreter.trace,
            )
        mapped = self.destination_map.get(value, value)
        destination = self.dictionary.destination(mapped)
        if destination is None:
            return RouteOutcome(
                RouteKind.UNSUPPORTED,
                detail=f"router returned undeclared destination {value!r}",
                trace=interpreter.trace,
            )
        return RouteOutcome(
            RouteKind.DESTINATION,
            destination=destination,
            matched=self.entrypoint,
            trace=interpreter.trace,
        )


def compile_router(
    source: str,
    *,
    link: CodeLink,
    dictionary: DataDictionary,
    artifact: ArtifactRef,
    limits: Limits,
    path: str,
) -> CodeProgram:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise RouterSyntaxError(
            f"router source is not valid Python syntax: {exc.msg}",
            lineno=exc.lineno,
        ) from exc
    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > limits.interpreter_max_ast_nodes:
        raise RouterLimitError(
            f"router has {node_count} AST nodes, exceeding "
            f"interpreter_max_ast_nodes={limits.interpreter_max_ast_nodes}"
        )
    function, constants = _check_module(tree, link, limits)
    return CodeProgram(
        artifact_ref=str(artifact),
        path=path,
        entrypoint=link.entrypoint,
        dictionary=dictionary,
        destination_map=dict(link.destination_map),
        field_map=dict(link.field_map),
        constants=constants,
        function=function,
        param=link.param,
        max_steps=limits.interpreter_max_steps,
        max_depth=limits.interpreter_max_depth,
        source=source,
    )


def _check_module(
    tree: ast.Module, link: CodeLink, limits: Limits
) -> tuple[ast.FunctionDef, dict[str, object]]:
    body = list(tree.body)
    index = 0
    if body and _is_docstring(body[0]):
        index = 1
    function: ast.FunctionDef | None = None
    constants: dict[str, object] = {}
    for statement in body[index:]:
        if isinstance(statement, ast.ImportFrom):
            if statement.module == "__future__":
                if any(alias.name != "annotations" for alias in statement.names):
                    raise RouterUnsupportedError(
                        "only '__future__ import annotations' is supported",
                        lineno=statement.lineno,
                    )
                continue
            if statement.module == "typing":
                continue
            raise RouterUnsupportedError(
                f"import from {statement.module!r} is not supported",
                lineno=statement.lineno,
            )
        if isinstance(statement, ast.Import):
            if all(alias.name == "typing" for alias in statement.names):
                continue
            raise RouterUnsupportedError(
                "only 'import typing' is supported", lineno=statement.lineno
            )
        if isinstance(statement, ast.Assign):
            name, value = _assignment(statement)
            constants[name] = value
            continue
        if isinstance(statement, ast.FunctionDef):
            if function is not None:
                raise RouterUnsupportedError(
                    "the router artifact must declare exactly one function",
                    lineno=statement.lineno,
                )
            function = statement
            continue
        raise RouterUnsupportedError(
            f"module statement {type(statement).__name__} is not supported",
            lineno=statement.lineno,
        )
    if function is None:
        raise RouterUnsupportedError("the router artifact declares no function")
    if function.name != link.entrypoint:
        raise RouterUnsupportedError(
            f"router function is {function.name!r}, but the link declares entrypoint "
            f"{link.entrypoint!r}",
            lineno=function.lineno,
        )
    _check_function(function, link, constants, limits)
    return function, constants


def _check_function(
    function: ast.FunctionDef,
    link: CodeLink,
    constants: Mapping[str, object],
    limits: Limits,
) -> None:
    if function.decorator_list:
        raise RouterUnsupportedError("decorators are not supported", lineno=function.lineno)
    arguments = function.args
    if (
        arguments.posonlyargs
        or len(arguments.args) != 1
        or arguments.vararg is not None
        or arguments.kwonlyargs
        or arguments.kwarg is not None
        or arguments.defaults
    ):
        raise RouterUnsupportedError(
            "the router must take exactly one positional parameter with no defaults",
            lineno=function.lineno,
        )
    parameter = arguments.args[0]
    if parameter.arg != link.param:
        raise RouterUnsupportedError(
            f"router parameter is {parameter.arg!r}, but the link declares {link.param!r}",
            lineno=function.lineno,
        )
    assigned = _assigned_names(function)
    if link.param in assigned:
        raise RouterUnsupportedError(
            "the router parameter cannot be reassigned", lineno=function.lineno
        )
    if _depth(function) > limits.interpreter_max_depth:
        raise RouterLimitError(
            f"router nesting exceeds interpreter_max_depth={limits.interpreter_max_depth}"
        )
    checker = _StatementChecker(link.param, constants, assigned)
    checker.block(function.body)


class _StatementChecker:
    def __init__(
        self, param: str, constants: Mapping[str, object], assigned: frozenset[str]
    ) -> None:
        self._param = param
        self._constants = constants
        self._assigned = assigned

    def block(self, statements: list[ast.stmt]) -> None:
        for statement in statements:
            self.statement(statement)

    def statement(self, statement: ast.stmt) -> None:
        if isinstance(statement, ast.Expr):
            if not _is_docstring(statement):
                raise RouterUnsupportedError(
                    "only docstring expression statements are supported",
                    lineno=statement.lineno,
                )
            return
        if isinstance(statement, ast.Assign):
            if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
                raise RouterUnsupportedError(
                    "only single-name assignments are supported",
                    lineno=statement.lineno,
                )
            if statement.targets[0].id == self._param:
                raise RouterUnsupportedError(
                    "the router parameter cannot be reassigned",
                    lineno=statement.lineno,
                )
            self.expression(statement.value)
            return
        if isinstance(statement, ast.If):
            self.expression(statement.test)
            self.block(statement.body)
            self.block(statement.orelse)
            return
        if isinstance(statement, ast.Return):
            if statement.value is not None:
                self.expression(statement.value)
            return
        if isinstance(statement, ast.Pass):
            return
        raise RouterUnsupportedError(
            f"statement {type(statement).__name__} is not supported",
            lineno=statement.lineno,
        )

    def expression(self, expression: ast.expr) -> None:
        if isinstance(expression, ast.Constant):
            if not _is_supported_constant(expression.value):
                raise RouterUnsupportedError(
                    f"literal {expression.value!r} is not supported",
                    lineno=expression.lineno,
                )
            return
        if isinstance(expression, ast.Name):
            if expression.id == self._param:
                raise RouterUnsupportedError(
                    "the ticket mapping cannot be used as a value; use "
                    'ticket["field"], ticket.get("field") or "field" in ticket',
                    lineno=expression.lineno,
                )
            if expression.id not in self._constants and expression.id not in self._assigned:
                raise RouterUnsupportedError(
                    f"name {expression.id!r} is not a module constant or local assignment",
                    lineno=expression.lineno,
                )
            return
        if isinstance(expression, ast.Subscript):
            if not _is_ticket(expression.value, self._param):
                raise RouterUnsupportedError(
                    "only ticket['field'] subscripting is supported",
                    lineno=expression.lineno,
                )
            if not isinstance(expression.slice, ast.Constant) or not isinstance(
                expression.slice.value, str
            ):
                raise RouterUnsupportedError(
                    "ticket keys must be string literals", lineno=expression.lineno
                )
            return
        if isinstance(expression, ast.Call):
            if not (
                isinstance(expression.func, ast.Attribute)
                and expression.func.attr == "get"
                and _is_ticket(expression.func.value, self._param)
            ):
                raise RouterUnsupportedError(
                    "only ticket.get('field') calls are supported",
                    lineno=expression.lineno,
                )
            if (
                len(expression.args) != 1
                or expression.keywords
                or not isinstance(expression.args[0], ast.Constant)
                or not isinstance(expression.args[0].value, str)
            ):
                raise RouterUnsupportedError(
                    "ticket.get requires exactly one string literal argument",
                    lineno=expression.lineno,
                )
            return
        if isinstance(expression, ast.Compare):
            _check_compare(expression, self._param)
            self.expression(expression.left)
            operator = expression.ops[0]
            if isinstance(operator, (ast.In, ast.NotIn)) and _is_ticket(
                expression.comparators[0], self._param
            ):
                return
            for comparator in expression.comparators:
                self.expression(comparator)
            return
        if isinstance(expression, ast.BoolOp):
            for value in expression.values:
                self.expression(value)
            return
        if isinstance(expression, ast.UnaryOp):
            if not isinstance(expression.op, ast.Not):
                raise RouterUnsupportedError(
                    "only 'not' unary operations are supported",
                    lineno=expression.lineno,
                )
            self.expression(expression.operand)
            return
        if isinstance(expression, (ast.Tuple, ast.List, ast.Set)):
            for element in expression.elts:
                _literal(element)
            return
        raise RouterUnsupportedError(
            f"expression {type(expression).__name__} is not supported",
            lineno=expression.lineno,
        )


def _check_compare(expression: ast.Compare, param: str) -> None:
    if len(expression.ops) != 1 or len(expression.comparators) != 1:
        raise RouterUnsupportedError(
            "exactly one comparison per Compare is supported",
            lineno=expression.lineno,
        )
    operator = expression.ops[0]
    if type(operator) not in _COMPARE_OPS:
        raise RouterUnsupportedError(
            f"comparison {type(operator).__name__} is not supported",
            lineno=expression.lineno,
        )
    if isinstance(operator, (ast.Is, ast.IsNot)):
        if not (_is_none(expression.left) or _is_none(expression.comparators[0])):
            raise RouterUnsupportedError(
                "'is'/'is not' is only supported against None",
                lineno=expression.lineno,
            )
    if isinstance(operator, (ast.In, ast.NotIn)):
        comparator = expression.comparators[0]
        if _is_ticket(comparator, param):
            if not (
                isinstance(expression.left, ast.Constant) and isinstance(expression.left.value, str)
            ):
                raise RouterUnsupportedError(
                    "'key in ticket' requires a string literal key",
                    lineno=expression.lineno,
                )
        elif not isinstance(comparator, (ast.Constant, ast.Tuple, ast.List, ast.Set)):
            raise RouterUnsupportedError(
                "membership requires a literal string or collection",
                lineno=expression.lineno,
            )


def _assignment(statement: ast.Assign) -> tuple[str, object]:
    if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
        raise RouterUnsupportedError(
            "only single-name assignments are supported", lineno=statement.lineno
        )
    return statement.targets[0].id, _literal(statement.value)


def _literal(expression: ast.expr) -> object:
    if isinstance(expression, ast.Constant):
        if not _is_supported_constant(expression.value):
            raise RouterUnsupportedError(
                f"literal {expression.value!r} is not supported",
                lineno=expression.lineno,
            )
        value = expression.value
        return _decimal_fraction(value) if isinstance(value, float) else value
    if (
        isinstance(expression, ast.UnaryOp)
        and isinstance(expression.op, ast.USub)
        and isinstance(expression.operand, ast.Constant)
        and isinstance(expression.operand.value, (int, float))
        and not isinstance(expression.operand.value, bool)
    ):
        value = expression.operand.value
        return -_decimal_fraction(value) if isinstance(value, float) else -value
    raise RouterUnsupportedError(
        "module constants and collection elements must be literal values",
        lineno=expression.lineno,
    )


def _assigned_names(function: ast.FunctionDef) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return frozenset(names)


def _depth(node: ast.AST) -> int:
    maximum = 1
    stack: list[tuple[ast.AST, int]] = [(node, 1)]
    while stack:
        current, depth = stack.pop()
        maximum = max(maximum, depth)
        for child in ast.iter_child_nodes(current):
            stack.append((child, depth + 1))
    return maximum


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _is_none(expression: ast.expr) -> bool:
    return isinstance(expression, ast.Constant) and expression.value is None


def _is_ticket(expression: ast.expr, param: str) -> bool:
    return isinstance(expression, ast.Name) and expression.id == param


def _is_supported_constant(value: object) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _decimal_fraction(value: float) -> Fraction:
    """Read a source float literal as its decimal value, not its binary encoding.

    The routing contract is built on exact rationals; ``Fraction(repr(29.5))``
    keeps ``29.5`` exactly at the declared decimal boundary.
    """
    return Fraction(repr(value))


class _Interpreter:
    def __init__(self, program: CodeProgram, values: Mapping[str, CellValue]) -> None:
        self._program = program
        self._values = values
        self._environment: dict[str, object] = dict(program.constants)
        self._steps = 0
        self._trace: list[str] = []

    @property
    def trace(self) -> tuple[str, ...]:
        return tuple(self._trace)

    def run(self) -> _Returned | None:
        return self._block(self._program.function.body)

    def _tick(self) -> None:
        self._steps += 1
        if self._steps > self._program.max_steps:
            raise _StepLimit

    def _block(self, statements: list[ast.stmt]) -> _Returned | None:
        for statement in statements:
            self._tick()
            returned = self._statement(statement)
            if returned is not None:
                return returned
        return None

    def _statement(self, statement: ast.stmt) -> _Returned | None:
        if isinstance(statement, ast.Expr):
            return None
        if isinstance(statement, ast.Assign):
            target = cast(ast.Name, statement.targets[0])
            self._environment[target.id] = self._eval(statement.value)
            return None
        if isinstance(statement, ast.If):
            outcome = _truthy(self._eval(statement.test))
            segment = ast.get_source_segment(self._program.source, statement.test) or ""
            rendered = " ".join(segment.split())
            self._trace.append(
                f"line {statement.lineno}: {'true' if outcome else 'false'} ({rendered})"
            )
            if outcome:
                return self._block(statement.body)
            return self._block(statement.orelse)
        if isinstance(statement, ast.Return):
            value = None if statement.value is None else self._eval(statement.value)
            return _Returned(value)
        return None

    def _eval(self, expression: ast.expr) -> object:
        self._tick()
        if isinstance(expression, ast.Constant):
            value = expression.value
            return _decimal_fraction(value) if isinstance(value, float) else value
        if isinstance(expression, ast.Name):
            if expression.id in self._environment:
                return self._environment[expression.id]
            raise _PyFailure("NameError", f"name {expression.id!r} is not defined")
        if isinstance(expression, ast.Subscript):
            key = cast(ast.Constant, expression.slice)
            return self._lookup(str(key.value), raising=True)
        if isinstance(expression, ast.Call):
            argument = cast(ast.Constant, expression.args[0])
            return self._lookup(str(argument.value), raising=False)
        if isinstance(expression, ast.Compare):
            return self._compare(expression)
        if isinstance(expression, ast.BoolOp):
            return self._boolean(expression)
        if isinstance(expression, ast.UnaryOp):
            return not _truthy(self._eval(expression.operand))
        if isinstance(expression, ast.Tuple):
            return tuple(self._eval(element) for element in expression.elts)
        if isinstance(expression, ast.List):
            return tuple(self._eval(element) for element in expression.elts)
        if isinstance(expression, ast.Set):
            return frozenset(self._eval(element) for element in expression.elts)
        raise _Unsupported(f"expression {type(expression).__name__} is not supported")

    def _lookup(self, key: str, *, raising: bool) -> object:
        field, _field_name, unit = self._field_for_key(key)
        if field is None:
            raise _Unsupported(f"ticket key {key!r} maps to an unknown field")
        cell = self._values.get(field.name)
        if cell is None or cell.state is not ValueState.PRESENT:
            if raising:
                raise _PyFailure("KeyError", repr(key))
            return None
        value = cell.value
        if unit is not None:
            if not isinstance(value, Fraction) or field.unit is None:
                raise _Unsupported(f"field {field.name!r} cannot be converted to {unit!r}")
            try:
                value = normalize(value, field.unit, unit)
            except UnitError as exc:
                raise _Unsupported(str(exc)) from exc
        return value

    def _field_for_key(self, key: str) -> tuple[FieldDecl | None, str | None, str | None]:
        return _field_for_key(self._program, key)

    def _compare(self, expression: ast.Compare) -> object:
        operator = expression.ops[0]
        left = self._eval(expression.left)
        right_node = expression.comparators[0]
        if isinstance(operator, (ast.In, ast.NotIn)) and _is_ticket(
            right_node, self._program.param
        ):
            left = cast(ast.Constant, expression.left)
            present = self._is_present(str(left.value))
            return present if isinstance(operator, ast.In) else not present
        right = self._eval(right_node)
        if isinstance(operator, ast.Eq):
            return left == right
        if isinstance(operator, ast.NotEq):
            return left != right
        if isinstance(operator, ast.Is):
            return left is right
        if isinstance(operator, ast.IsNot):
            return left is not right
        if isinstance(operator, (ast.In, ast.NotIn)):
            member = _membership(left, right)
            return member if isinstance(operator, ast.In) else not member
        return _ordered(_COMPARE_OPS[type(operator)], left, right)

    def _boolean(self, expression: ast.BoolOp) -> object:
        if isinstance(expression.op, ast.And):
            result: object = True
            for value in expression.values:
                result = self._eval(value)
                if not _truthy(result):
                    return result
            return result
        result = False
        for value in expression.values:
            result = self._eval(value)
            if _truthy(result):
                return result
        return result

    def _is_present(self, key: str) -> bool:
        field, _, _ = self._field_for_key(key)
        if field is None:
            raise _Unsupported(f"ticket key {key!r} maps to an unknown field")
        cell = self._values.get(field.name)
        return cell is not None and cell.state is ValueState.PRESENT


def _membership(left: object, right: object) -> bool:
    if isinstance(right, str):
        if not isinstance(left, str):
            raise _PyFailure(
                "TypeError",
                f"'in <string>' requires str as left operand, not {_type_name(left)}",
            )
        return left in right
    if isinstance(right, (tuple, frozenset, list)):
        return left in right
    raise _PyFailure(
        "TypeError",
        f"argument of type {_type_name(right)} is not iterable",
    )


def _ordered(operator: str, left: object, right: object) -> bool:
    if left is None or right is None:
        raise _PyFailure(
            "TypeError",
            f"'{operator}' not supported between instances of "
            f"{_type_name(left)!r} and {_type_name(right)!r}",
        )
    if isinstance(left, (int, Fraction)) and isinstance(right, (int, Fraction)):
        return _numeric_compare(operator, left, right)
    if isinstance(left, str) and isinstance(right, str):
        return _text_compare(operator, left, right)
    raise _PyFailure(
        "TypeError",
        f"'{operator}' not supported between instances of "
        f"{_type_name(left)!r} and {_type_name(right)!r}",
    )


def _numeric_compare(operator: str, left: int | Fraction, right: int | Fraction) -> bool:
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == ">":
        return left > right
    return left >= right


def _text_compare(operator: str, left: str, right: str) -> bool:
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == ">":
        return left > right
    return left >= right


def _truthy(value: object) -> bool:
    if value is None or value is False:
        return False
    if value is True:
        return True
    if isinstance(value, (int, Fraction)):
        return bool(value)
    if isinstance(value, str):
        return value != ""
    if isinstance(value, (tuple, frozenset)):
        return len(value) > 0
    return True


def _type_name(value: object) -> str:
    return _TYPE_NAMES.get(type(value), type(value).__name__)


def _field_for_key(
    program: CodeProgram, key: str
) -> tuple[FieldDecl | None, str | None, str | None]:
    entry = program.field_map.get(key)
    if entry is not None:
        field_name: str | None = entry.field
        unit = entry.unit
    else:
        field_name = program.dictionary.resolve_field(key) or key
        unit = None
    return program.dictionary.field(field_name or key), field_name, unit


def _ticket_key(node: ast.expr, param: str) -> str | None:
    if isinstance(node, ast.Subscript) and _is_ticket(node.value, param):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value
        return None
    if isinstance(node, ast.Call):
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and function.attr == "get"
            and _is_ticket(function.value, param)
            and len(node.args) == 1
            and not node.keywords
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node.args[0].value
    return None


def _static_value(node: ast.expr, environment: Mapping[str, object]) -> object:
    if isinstance(node, ast.Constant):
        return node.value if _is_supported_constant(node.value) else _UNKNOWN
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, int)
        and not isinstance(node.operand.value, bool)
    ):
        return -node.operand.value
    if isinstance(node, ast.Name):
        return environment.get(node.id, _UNKNOWN)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        values = tuple(_static_value(element, environment) for element in node.elts)
        if any(value is _UNKNOWN for value in values):
            return _UNKNOWN
        return values
    return _UNKNOWN


def _static_environment(program: CodeProgram) -> dict[str, object]:
    """Resolve names bound exactly once to an unambiguous literal value.

    Names assigned in several branches are omitted rather than guessed; this
    environment feeds domain hints and diagnosis only, never routing.
    """
    environment: dict[str, object] = dict(program.constants)
    assignments: dict[str, list[object]] = {}
    for node in ast.walk(program.function):
        if not isinstance(node, ast.Assign):
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name):
            value = _static_value(node.value, environment)
            if value is not _UNKNOWN:
                assignments.setdefault(target.id, []).append(value)
    for name, values in assignments.items():
        distinct = {(type(value), value) for value in values}
        if len(distinct) == 1:
            environment[name] = values[0]
    return environment


def _numbers(value: object) -> tuple[Fraction, ...]:
    if isinstance(value, bool):
        return ()
    if isinstance(value, Fraction):
        return (value,)
    if isinstance(value, int):
        return (Fraction(value),)
    if isinstance(value, float):
        return (_decimal_fraction(value),)
    if isinstance(value, tuple):
        numbers: list[Fraction] = []
        for item in value:
            if isinstance(item, bool):
                return ()
            if isinstance(item, Fraction):
                numbers.append(item)
            elif isinstance(item, int):
                numbers.append(Fraction(item))
            elif isinstance(item, float):
                numbers.append(_decimal_fraction(item))
            else:
                return ()
        return tuple(numbers)
    return ()


def _texts(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, tuple):
        if all(isinstance(item, str) for item in value):
            return cast(tuple[str, ...], value)
    return ()


def _convert(number: Fraction, unit: str | None, field: FieldDecl) -> Fraction | None:
    if unit is None:
        return number
    if field.unit is None:
        return None
    try:
        return normalize(number, unit, field.unit)
    except UnitError:
        return None


def _domain_hints(program: CodeProgram) -> CodeDomain:
    environment = _static_environment(program)
    cuts: dict[str, set[Fraction]] = {}
    literals: dict[str, set[str | bool]] = {}
    for node in ast.walk(program.function):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        operator = node.ops[0]
        if isinstance(operator, (ast.In, ast.NotIn)) and _is_ticket(
            node.comparators[0], program.param
        ):
            continue
        left_key = _ticket_key(node.left, program.param)
        right_key = _ticket_key(node.comparators[0], program.param)
        if left_key is not None and right_key is None:
            key, literal_node = left_key, node.comparators[0]
        elif right_key is not None and left_key is None:
            key, literal_node = right_key, node.left
        else:
            continue
        value = _static_value(literal_node, environment)
        if value is _UNKNOWN:
            continue
        field, _field_name, unit = _field_for_key(program, key)
        if field is None:
            continue
        if field.type in {FieldType.INTEGER, FieldType.DECIMAL}:
            for number in _numbers(value):
                converted = _convert(number, unit, field)
                if converted is not None:
                    cuts.setdefault(field.name, set()).add(converted)
        elif field.type is FieldType.STRING:
            for text in _texts(value):
                literals.setdefault(field.name, set()).add(text)
    return CodeDomain(
        cuts={name: tuple(sorted(values)) for name, values in cuts.items()},
        literals={name: tuple(sorted(values, key=repr)) for name, values in literals.items()},
    )


def _routing_signature(program: CodeProgram) -> tuple[CodeRule, ...] | None:
    environment = _static_environment(program)
    rules = _chain(program, program.function.body, environment)
    return None if rules is None else tuple(rules)


def _chain(
    program: CodeProgram,
    statements: list[ast.stmt],
    environment: Mapping[str, object],
) -> list[CodeRule] | None:
    rules: list[CodeRule] = []
    for statement in statements:
        if isinstance(statement, ast.Expr) and _is_docstring(statement):
            continue
        if isinstance(statement, ast.If):
            condition = _ir_from_ast(program, statement.test, environment)
            destination = _body_destination(program, statement.body, environment)
            if condition is None or destination is None:
                return None
            start, end, col_start, col_end, excerpt = _span(program, statement)
            rules.append(CodeRule(condition, destination, start, end, col_start, col_end, excerpt))
            if statement.orelse:
                nested = _chain(program, statement.orelse, environment)
                if nested is None:
                    return None
                rules.extend(nested)
            continue
        if isinstance(statement, ast.Return):
            destination = _return_destination(program, statement, environment)
            if destination is None:
                return None
            start, end, col_start, col_end, excerpt = _span(program, statement)
            rules.append(
                CodeRule(Const(value=True), destination, start, end, col_start, col_end, excerpt)
            )
            continue
        return None
    return rules


def _span(program: CodeProgram, node: ast.AST) -> tuple[int, int, int, int, str]:
    start = getattr(node, "lineno", 1)
    end = getattr(node, "end_lineno", None) or start
    col_start = getattr(node, "col_offset", 0) + 1
    end_offset = getattr(node, "end_col_offset", None)
    col_end = (end_offset + 1) if end_offset is not None else col_start
    excerpt = ast.get_source_segment(program.source, node) or ""
    return start, end, col_start, col_end, excerpt


def _comparison_facts(program: CodeProgram) -> tuple[CmpFact, ...]:
    environment = _static_environment(program)
    facts: list[CmpFact] = []
    for node in ast.walk(program.function):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        comparison = _ir_compare(program, node, environment)
        if not isinstance(comparison, (Cmp, Eq, Ne)):
            continue
        start, end, col_start, col_end, excerpt = _span(program, node)
        facts.append(CmpFact(comparison, start, end, col_start, col_end, excerpt))
    return tuple(facts)


def _body_destination(
    program: CodeProgram,
    statements: list[ast.stmt],
    environment: Mapping[str, object],
) -> str | None:
    body = [item for item in statements if not _is_docstring(item)]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return None
    return _return_destination(program, body[0], environment)


def _return_destination(
    program: CodeProgram,
    statement: ast.Return,
    environment: Mapping[str, object],
) -> str | None:
    if statement.value is None:
        return None
    value = _static_value(statement.value, environment)
    if not isinstance(value, str):
        return None
    return program.dictionary.destination(program.destination_map.get(value, value))


def _ir_from_ast(
    program: CodeProgram, node: ast.expr, environment: Mapping[str, object]
) -> Expr | None:
    if isinstance(node, ast.Compare):
        return _ir_compare(program, node, environment)
    if isinstance(node, ast.BoolOp):
        items = tuple(_ir_from_ast(program, value, environment) for value in node.values)
        if any(item is None for item in items):
            return None
        present = tuple(item for item in items if item is not None)
        if len(present) == 1:
            return present[0]
        return And(items=present) if isinstance(node.op, ast.And) else Or(items=present)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        inner = _ir_from_ast(program, node.operand, environment)
        return None if inner is None else NotNode(item=inner)
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return Const(value=node.value)
    if isinstance(node, ast.Subscript):
        key = _ticket_key(node, program.param)
        if key is None:
            return None
        field, _field_name, unit = _field_for_key(program, key)
        if field is None or unit is not None or field.type is not FieldType.BOOLEAN:
            return None
        # ``ticket["flag"]`` as an if-condition is true only for a present True.
        return NotNode(item=NotTrue(item=Eq(field=field.name, value=True)))
    if isinstance(node, ast.Name):
        value = environment.get(node.id, _UNKNOWN)
        return Const(value=value) if isinstance(value, bool) else None
    return None


def _ir_compare(
    program: CodeProgram, node: ast.Compare, environment: Mapping[str, object]
) -> Expr | None:
    if len(node.ops) != 1:
        return None
    operator = node.ops[0]
    if isinstance(operator, (ast.In, ast.NotIn)) and _is_ticket(node.comparators[0], program.param):
        key_node = node.left
        if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
            return None
        field, _field_name, _unit = _field_for_key(program, key_node.value)
        if field is None:
            return None
        missing = Missing(field=field.name)
        return NotNode(item=missing) if isinstance(operator, ast.In) else missing
    left_key = _ticket_key(node.left, program.param)
    right_key = _ticket_key(node.comparators[0], program.param)
    if left_key is not None and right_key is None:
        key, literal_node = left_key, node.comparators[0]
        flipped = False
    elif right_key is not None and left_key is None:
        key, literal_node = right_key, node.left
        flipped = True
    else:
        return None
    field, _field_name, unit = _field_for_key(program, key)
    if field is None:
        return None
    value = _static_value(literal_node, environment)
    if value is _UNKNOWN:
        return None
    if isinstance(operator, (ast.In, ast.NotIn)):
        texts = _texts(value)
        if field.type is not FieldType.STRING or not texts:
            return None
        values = frozenset(texts)
        return (
            In(field=field.name, values=values)
            if isinstance(operator, ast.In)
            else NotIn(field=field.name, values=values)
        )
    if field.type in {FieldType.INTEGER, FieldType.DECIMAL}:
        numbers = _numbers(value)
        if len(numbers) != 1:
            return None
        converted = _convert(numbers[0], unit, field)
        op_code = _IR_OPS.get(type(operator))
        if converted is None or op_code is None:
            return None
        return Cmp(
            field=field.name,
            op=_FLIPPED[op_code] if flipped else op_code,
            value=converted,
            unit=field.unit,
        )
    if isinstance(value, str):
        if isinstance(operator, ast.Eq):
            return Eq(field=field.name, value=value)
        if isinstance(operator, ast.NotEq):
            return Ne(field=field.name, value=value)
        return None
    if isinstance(value, bool):
        if isinstance(operator, ast.Eq):
            return Eq(field=field.name, value=value)
        if isinstance(operator, ast.NotEq):
            return Ne(field=field.name, value=value)
    return None


__all__ = [
    "CmpFact",
    "CodeDomain",
    "CodeProgram",
    "CodeRule",
    "RouterCodeError",
    "RouterLimitError",
    "RouterSyntaxError",
    "RouterUnsupportedError",
    "compile_router",
]
