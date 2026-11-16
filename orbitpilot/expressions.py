"""Safe evaluation of ``when`` expressions.

Expressions are parsed with :mod:`ast` and evaluated against a whitelist
of node types (boolean logic, arithmetic, comparisons, names, constants).
No attribute access, calls, subscripts, or builtins are available.

A name that has not been produced yet (e.g. a ``store_as`` variable from
a step that has not run) raises :class:`UndefinedVariable` so the caller
can record the step as *skipped* instead of crashing.
"""

from __future__ import annotations

import ast


class UndefinedVariable(Exception):
    """A ``when`` expression referenced a variable that is not in the store."""


class InvalidExpression(ValueError):
    """The expression uses syntax outside the supported subset."""


_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.And,
    ast.Or,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)


def evaluate(expression, variables):
    """Evaluate ``expression`` against ``variables`` and return a bool.

    Raises :class:`UndefinedVariable` for unknown names and
    :class:`InvalidExpression` for unsupported syntax.
    """
    try:
        tree = ast.parse(str(expression), mode="eval")
    except SyntaxError as exc:
        raise InvalidExpression("cannot parse expression: %s" % (exc,)) from exc
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise InvalidExpression(
                "unsupported expression element: %s" % type(node).__name__
            )
        if isinstance(node, ast.Name) and node.id not in variables:
            raise UndefinedVariable(node.id)
    code = compile(tree, "<when>", "eval")
    return bool(eval(code, {"__builtins__": {}}, dict(variables)))
