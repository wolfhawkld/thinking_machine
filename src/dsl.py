"""A small, deterministic expression DSL used by the synthetic worlds.

The experiment deliberately keeps the hypothesis language finite and executable.
Expressions are represented as immutable tuples rather than executable Python
objects.  For example, ``x1 + x2`` is represented as::

    ("add", ("var", "x1"), ("var", "x2"))

Predicates have the analogous ``("gt", left, right)`` or ``("eq", left,
right)`` representation.  This makes serialisation, canonicalisation and
hashing deterministic and keeps candidate evaluation sandbox-free.
"""

from __future__ import annotations

import hashlib
import itertools
import re
from typing import Any, Iterable, Mapping, Sequence, Tuple, Union


Point = Tuple[int, int, int]
Expr = tuple[Any, ...]
Predicate = tuple[Any, ...]
AST = Union[Expr, Predicate]

VARIABLES: tuple[str, ...] = ("x1", "x2", "x3")
CONSTANTS: tuple[int, ...] = (-3, -2, -1, 0, 1, 2, 3)
DOMAIN_VALUES: tuple[int, ...] = (-2, -1, 0, 1, 2)
DOMAIN: tuple[Point, ...] = tuple(itertools.product(DOMAIN_VALUES, repeat=3))

DEFAULT_MAX_DEPTH = 5
DEFAULT_MAX_NODES = 31
DEFAULT_OUTPUT_BOUND = 100

_EXPR_LEAVES = {"var", "const"}
_EXPR_UNARY = {"neg"}
_EXPR_BINARY = {"add", "sub", "mul"}
_EXPR_TERNARY = {"ite"}
_PREDICATES = {"gt", "eq"}
_TOKEN_RE = re.compile(r"\(|\)|[^\s()]+")


class DSLValidationError(ValueError):
    """Raised when an AST is not a valid expression in the experiment DSL."""


# A shorter alias is convenient for callers that do not need the longer name.
ValidationError = DSLValidationError


def _require_expr_tuple(node: Any) -> Expr:
    if not isinstance(node, tuple) or not node or not isinstance(node[0], str):
        raise DSLValidationError(f"expected an AST tuple, got {node!r}")
    return node


def _validate_expr_shape(node: Any) -> None:
    """Validate tuple shape and symbols, without evaluating the AST."""

    node = _require_expr_tuple(node)
    op = node[0]
    if op == "var":
        if len(node) != 2 or node[1] not in VARIABLES:
            raise DSLValidationError(f"invalid variable node: {node!r}")
        return
    if op == "const":
        if len(node) != 2 or type(node[1]) is not int or node[1] not in CONSTANTS:
            raise DSLValidationError(f"invalid constant node: {node!r}")
        return
    if op in _EXPR_UNARY:
        if len(node) != 2:
            raise DSLValidationError(f"invalid unary node: {node!r}")
        _validate_expr_shape(node[1])
        return
    if op in _EXPR_BINARY:
        if len(node) != 3:
            raise DSLValidationError(f"invalid binary node: {node!r}")
        _validate_expr_shape(node[1])
        _validate_expr_shape(node[2])
        return
    if op == "ite":
        if len(node) != 4:
            raise DSLValidationError(f"invalid ite node: {node!r}")
        _validate_predicate_shape(node[1])
        _validate_expr_shape(node[2])
        _validate_expr_shape(node[3])
        return
    raise DSLValidationError(f"unknown expression operator {op!r}")


def _validate_predicate_shape(node: Any) -> None:
    node = _require_expr_tuple(node)
    op = node[0]
    if op not in _PREDICATES or len(node) != 3:
        raise DSLValidationError(f"invalid predicate node: {node!r}")
    _validate_expr_shape(node[1])
    _validate_expr_shape(node[2])


def canonicalize(node: AST) -> AST:
    """Return a recursively canonical AST.

    ``add`` and ``mul`` are commutative in the DSL, so their children are
    ordered by their canonical S-expression.  No algebraic simplification is
    performed: structural variants remain distinct unless the grammar itself
    makes them identical.
    """

    _require_expr_tuple(node)
    op = node[0]
    if op in _EXPR_LEAVES:
        _validate_expr_shape(node)
        if op == "const":
            return (op, int(node[1]))
        return (op, node[1])
    if op == "neg":
        if len(node) != 2:
            raise DSLValidationError(f"invalid neg node: {node!r}")
        return (op, canonicalize(node[1]))
    if op in {"add", "mul", "sub"}:
        if len(node) != 3:
            raise DSLValidationError(f"invalid {op} node: {node!r}")
        left = canonicalize(node[1])
        right = canonicalize(node[2])
        if op in {"add", "mul"}:
            ordered = sorted((left, right), key=to_sexpr)
            left, right = ordered
        return (op, left, right)
    if op == "ite":
        if len(node) != 4:
            raise DSLValidationError(f"invalid ite node: {node!r}")
        return (
            op,
            canonicalize_predicate(node[1]),
            canonicalize(node[2]),
            canonicalize(node[3]),
        )
    if op in _PREDICATES:
        return canonicalize_predicate(node)
    raise DSLValidationError(f"unknown AST operator {op!r}")


def canonicalize_predicate(node: Predicate) -> Predicate:
    """Canonicalise a predicate node used by ``ite``."""

    _validate_predicate_shape(node)
    op = node[0]
    left = canonicalize(node[1])
    right = canonicalize(node[2])
    # Keep predicate operand order intact.  The experiment only promises
    # canonical ordering for add/mul, and gt is not commutative.
    return (op, left, right)


def _sexpr(node: AST) -> str:
    op = node[0]
    if op == "var":
        return f"(var {node[1]})"
    if op == "const":
        return f"(const {node[1]})"
    if op in _EXPR_UNARY:
        return f"({op} {_sexpr(node[1])})"
    if op in _EXPR_BINARY:
        return f"({op} {_sexpr(node[1])} {_sexpr(node[2])})"
    if op == "ite":
        return f"(ite {_sexpr(node[1])} {_sexpr(node[2])} {_sexpr(node[3])})"
    if op in _PREDICATES:
        return f"({op} {_sexpr(node[1])} {_sexpr(node[2])})"
    raise DSLValidationError(f"unknown AST operator {op!r}")


def to_sexpr(node: AST) -> str:
    """Serialise an AST to its canonical S-expression form."""

    canonical = canonicalize(node)
    return _sexpr(canonical)


class _Parser:
    def __init__(self, tokens: Sequence[str]):
        self.tokens = tokens
        self.index = 0

    def _take(self) -> str:
        if self.index >= len(self.tokens):
            raise DSLValidationError("unexpected end of S-expression")
        token = self.tokens[self.index]
        self.index += 1
        return token

    def _expect(self, token: str) -> None:
        got = self._take()
        if got != token:
            raise DSLValidationError(f"expected {token!r}, got {got!r}")

    def parse_expr(self) -> Expr:
        self._expect("(")
        op = self._take()
        if op == "var":
            variable = self._take()
            self._expect(")")
            node: Expr = (op, variable)
        elif op == "const":
            token = self._take()
            try:
                value = int(token)
            except ValueError as exc:
                raise DSLValidationError(f"invalid integer constant {token!r}") from exc
            self._expect(")")
            node = (op, value)
        elif op in _EXPR_UNARY:
            child = self.parse_expr()
            self._expect(")")
            node = (op, child)
        elif op in _EXPR_BINARY:
            left = self.parse_expr()
            right = self.parse_expr()
            self._expect(")")
            node = (op, left, right)
        elif op == "ite":
            predicate = self.parse_predicate()
            then_branch = self.parse_expr()
            else_branch = self.parse_expr()
            self._expect(")")
            node = (op, predicate, then_branch, else_branch)
        else:
            raise DSLValidationError(f"unknown expression operator {op!r}")
        _validate_expr_shape(node)
        return node

    def parse_predicate(self) -> Predicate:
        self._expect("(")
        op = self._take()
        if op not in _PREDICATES:
            raise DSLValidationError(f"unknown predicate operator {op!r}")
        left = self.parse_expr()
        right = self.parse_expr()
        self._expect(")")
        node: Predicate = (op, left, right)
        _validate_predicate_shape(node)
        return node


def parse_sexpr(text: str) -> Expr:
    """Parse one DSL S-expression and return its canonical AST."""

    if not isinstance(text, str):
        raise DSLValidationError("S-expression must be text")
    tokens = _TOKEN_RE.findall(text)
    parser = _Parser(tokens)
    if not tokens:
        raise DSLValidationError("empty S-expression")
    result = parser.parse_expr()
    if parser.index != len(tokens):
        raise DSLValidationError("trailing tokens after S-expression")
    return canonicalize(result)  # type: ignore[return-value]


def depth(node: AST) -> int:
    """Return AST depth, counting a leaf as depth one."""

    _require_expr_tuple(node)
    op = node[0]
    if op in _EXPR_LEAVES:
        _validate_expr_shape(node)
        return 1
    if op == "neg":
        return 1 + depth(node[1])
    if op in _EXPR_BINARY:
        return 1 + max(depth(node[1]), depth(node[2]))
    if op == "ite":
        return 1 + max(
            predicate_depth(node[1]), depth(node[2]), depth(node[3])
        )
    if op in _PREDICATES:
        return predicate_depth(node)  # useful when called directly on P
    raise DSLValidationError(f"unknown AST operator {op!r}")


def predicate_depth(node: Predicate) -> int:
    _validate_predicate_shape(node)
    return 1 + max(depth(node[1]), depth(node[2]))


def node_count(node: AST) -> int:
    """Return the number of expression and predicate nodes in ``node``."""

    _require_expr_tuple(node)
    op = node[0]
    if op in _EXPR_LEAVES:
        _validate_expr_shape(node)
        return 1
    if op == "neg":
        return 1 + node_count(node[1])
    if op in _EXPR_BINARY:
        return 1 + node_count(node[1]) + node_count(node[2])
    if op == "ite":
        return 1 + predicate_node_count(node[1]) + node_count(node[2]) + node_count(node[3])
    if op in _PREDICATES:
        return predicate_node_count(node)  # useful when called directly on P
    raise DSLValidationError(f"unknown AST operator {op!r}")


def predicate_node_count(node: Predicate) -> int:
    _validate_predicate_shape(node)
    return 1 + node_count(node[1]) + node_count(node[2])


def variables_used(node: AST) -> frozenset[str]:
    """Return all variables occurring in an expression or predicate."""

    _require_expr_tuple(node)
    op = node[0]
    if op == "var":
        _validate_expr_shape(node)
        return frozenset((node[1],))
    if op == "const":
        _validate_expr_shape(node)
        return frozenset()
    if op == "neg":
        return variables_used(node[1])
    if op in _EXPR_BINARY:
        return variables_used(node[1]) | variables_used(node[2])
    if op == "ite":
        return (
            variables_used(node[1])
            | variables_used(node[2])
            | variables_used(node[3])
        )
    if op in _PREDICATES:
        return variables_used(node[1]) | variables_used(node[2])
    raise DSLValidationError(f"unknown AST operator {op!r}")


def operator_types(node: AST) -> frozenset[str]:
    """Return operator names occurring in an expression or predicate."""

    _require_expr_tuple(node)
    op = node[0]
    if op in _EXPR_LEAVES:
        _validate_expr_shape(node)
        return frozenset()
    if op == "neg":
        return frozenset((op,)) | operator_types(node[1])
    if op in _EXPR_BINARY:
        return frozenset((op,)) | operator_types(node[1]) | operator_types(node[2])
    if op == "ite":
        return (
            frozenset((op,))
            | operator_types(node[1])
            | operator_types(node[2])
            | operator_types(node[3])
        )
    if op in _PREDICATES:
        return frozenset((op,)) | operator_types(node[1]) | operator_types(node[2])
    raise DSLValidationError(f"unknown AST operator {op!r}")


def _evaluate_predicate(node: Predicate, env: Mapping[str, int]) -> bool:
    _validate_predicate_shape(node)
    op = node[0]
    left = evaluate(node[1], env)
    right = evaluate(node[2], env)
    if op == "gt":
        return left > right
    return left == right


def evaluate(node: Expr, env: Mapping[str, int] | Sequence[int]) -> int:
    """Evaluate an expression in an environment.

    ``env`` may be a mapping with keys ``x1``/``x2``/``x3`` or a length-three
    sequence in that order.  No output-bound clipping is performed here;
    callers that need the experiment's bound should use ``validate_expr``.
    """

    node = _require_expr_tuple(node)
    op = node[0]
    if isinstance(env, Mapping):
        values: Mapping[str, int] = env
    else:
        if len(env) != 3:
            raise DSLValidationError("sequence environments must have length three")
        values = dict(zip(VARIABLES, env))
    if op == "var":
        _validate_expr_shape(node)
        try:
            value = values[node[1]]
        except KeyError as exc:
            raise DSLValidationError(f"missing value for {node[1]}") from exc
        if type(value) is not int:
            raise DSLValidationError("environment values must be integers")
        return value
    if op == "const":
        _validate_expr_shape(node)
        return node[1]
    if op == "neg":
        return -evaluate(node[1], values)
    if op == "add":
        return evaluate(node[1], values) + evaluate(node[2], values)
    if op == "sub":
        return evaluate(node[1], values) - evaluate(node[2], values)
    if op == "mul":
        return evaluate(node[1], values) * evaluate(node[2], values)
    if op == "ite":
        return evaluate(node[2], values) if _evaluate_predicate(node[1], values) else evaluate(node[3], values)
    raise DSLValidationError(f"cannot evaluate operator {op!r}")


def behavior_vector(
    node: Expr,
    domain: Iterable[Point] = DOMAIN,
) -> tuple[int, ...]:
    """Evaluate ``node`` over ``domain`` in the supplied iteration order."""

    canonical = canonicalize(node)
    if canonical[0] in _PREDICATES:
        raise DSLValidationError("behavior vectors require an expression, not a predicate")
    return tuple(evaluate(canonical, point) for point in domain)


def canonical_hash(node: AST) -> str:
    """SHA-256 hash of the canonical S-expression."""

    return hashlib.sha256(to_sexpr(node).encode("utf-8")).hexdigest()


def behavior_hash(node: Expr, domain: Iterable[Point] = DOMAIN) -> str:
    """SHA-256 hash of the integer behavior vector over ``domain``."""

    vector = behavior_vector(node, domain)
    payload = ",".join(str(value) for value in vector).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def is_behaviorally_equivalent(
    left: Expr,
    right: Expr,
    domain: Iterable[Point] = DOMAIN,
) -> bool:
    """Return whether two expressions agree on every point in ``domain``."""

    return behavior_vector(left, domain) == behavior_vector(right, domain)


def validate_expr(
    node: Expr,
    *,
    domain: Iterable[Point] = DOMAIN,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    output_bound: int | None = DEFAULT_OUTPUT_BOUND,
) -> None:
    """Validate shape, size and optional full-domain output bounds.

    The function returns ``None`` on success and raises
    :class:`DSLValidationError` on failure.  This makes it suitable for the
    programmatic verifier and for rejection sampling in the world generator.
    """

    _validate_expr_shape(node)
    canonical = canonicalize(node)
    if depth(canonical) > max_depth:
        raise DSLValidationError(
            f"expression depth {depth(canonical)} exceeds {max_depth}"
        )
    if node_count(canonical) > max_nodes:
        raise DSLValidationError(
            f"expression node count {node_count(canonical)} exceeds {max_nodes}"
        )
    if output_bound is not None:
        values = behavior_vector(canonical, domain)
        if any(type(value) is not int for value in values):
            raise DSLValidationError("expression outputs must be integers")
        if any(abs(value) > output_bound for value in values):
            raise DSLValidationError(
                f"expression output exceeds bound {output_bound}"
            )


def hidden_law_constraints(
    node: Expr,
    *,
    domain: Iterable[Point] = DOMAIN,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    output_bound: int | None = DEFAULT_OUTPUT_BOUND,
) -> None:
    """Validate the additional constraints used for generated hidden laws."""

    validate_expr(
        node,
        domain=domain,
        max_depth=max_depth,
        max_nodes=max_nodes,
        output_bound=output_bound,
    )
    if len(variables_used(node)) < 2:
        raise DSLValidationError("hidden law must use at least two variables")
    if len(operator_types(node)) < 2:
        raise DSLValidationError("hidden law must use at least two operator types")
    values = behavior_vector(node, domain)
    if len(set(values)) <= 1:
        raise DSLValidationError("constant hidden laws are rejected")
    if node[0] == "var":
        raise DSLValidationError("direct identity hidden laws are rejected")


__all__ = [
    "AST",
    "CONSTANTS",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_NODES",
    "DEFAULT_OUTPUT_BOUND",
    "DOMAIN",
    "DOMAIN_VALUES",
    "DSLValidationError",
    "Expr",
    "Point",
    "Predicate",
    "VARIABLES",
    "ValidationError",
    "behavior_hash",
    "behavior_vector",
    "canonical_hash",
    "canonicalize",
    "canonicalize_predicate",
    "depth",
    "evaluate",
    "hidden_law_constraints",
    "is_behaviorally_equivalent",
    "node_count",
    "operator_types",
    "parse_sexpr",
    "predicate_depth",
    "predicate_node_count",
    "to_sexpr",
    "validate_expr",
    "variables_used",
]
