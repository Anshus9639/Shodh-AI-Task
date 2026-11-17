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
from typing import cast

from verityflow.config import Limits
from verityflow.ir.evaluate import CellValue, ValueState
from verityflow.ir.units import UnitError, normalize
from verityflow.models.common import ArtifactRef
from verityflow.models.dictionary import DataDictionary, FieldDecl
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
    name: str = "code"

    def route(self, values: Mapping[str, CellValue]) -> RouteOutcome:
        try:
            returned = _Interpreter(self, values).run()
        except _StepLimit:
            return RouteOutcome(
                RouteKind.STEP_LIMIT,
                detail=f"router exceeded interpreter_max_steps={self.max_steps}",
            )
        except _Unsupported as exc:
            return RouteOutcome(RouteKind.UNSUPPORTED, detail=str(exc))
        except _PyFailure as exc:
            return RouteOutcome(RouteKind.ERROR, detail=f"{exc.kind}: {exc.message}")
        if returned is None:
            return RouteOutcome(
                RouteKind.FALL_THROUGH,
                detail="router completed without returning a destination",
            )
        value = returned.value
        if value is None:
            return RouteOutcome(RouteKind.NO_MATCH, detail="router returned None")
        if not isinstance(value, str):
            return RouteOutcome(
                RouteKind.UNSUPPORTED,
                detail=f"router returned {_type_name(value)}, not a destination string",
            )
        mapped = self.destination_map.get(value, value)
        destination = self.dictionary.destination(mapped)
        if destination is None:
            return RouteOutcome(
                RouteKind.UNSUPPORTED,
                detail=f"router returned undeclared destination {value!r}",
            )
        return RouteOutcome(
            RouteKind.DESTINATION,
            destination=destination,
            matched=self.entrypoint,
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
        return expression.value
    if (
        isinstance(expression, ast.UnaryOp)
        and isinstance(expression.op, ast.USub)
        and isinstance(expression.operand, ast.Constant)
        and isinstance(expression.operand.value, int)
        and not isinstance(expression.operand.value, bool)
    ):
        return -expression.operand.value
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
    return value is None or isinstance(value, (bool, int, str))


class _Interpreter:
    def __init__(self, program: CodeProgram, values: Mapping[str, CellValue]) -> None:
        self._program = program
        self._values = values
        self._environment: dict[str, object] = dict(program.constants)
        self._steps = 0

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
            if _truthy(self._eval(statement.test)):
                return self._block(statement.body)
            return self._block(statement.orelse)
        if isinstance(statement, ast.Return):
            value = None if statement.value is None else self._eval(statement.value)
            return _Returned(value)
        return None

    def _eval(self, expression: ast.expr) -> object:
        self._tick()
        if isinstance(expression, ast.Constant):
            return expression.value
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
        entry = self._program.field_map.get(key)
        if entry is not None:
            field_name = entry.field
            unit = entry.unit
        else:
            field_name = self._program.dictionary.resolve_field(key) or key
            unit = None
        return self._program.dictionary.field(field_name), field_name, unit

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


__all__ = [
    "CodeProgram",
    "RouterCodeError",
    "RouterLimitError",
    "RouterSyntaxError",
    "RouterUnsupportedError",
    "compile_router",
]
