"""Programmatic verification utilities for the synthetic micro-science world.

The verifier deliberately contains no language-model judging.  Candidates are parsed
into the tuple AST used by :mod:`src.dsl`, executed on labelled examples, and ranked
using probe accuracy followed by the frozen structural tie breakers from the
experiment specification.

The module is intentionally small and dependency-free.  ``dsl`` is imported lazily
so that the public helpers remain useful in isolation (and so unit tests can provide
an equivalent tuple AST implementation).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Optional, Sequence


class VerificationError(ValueError):
    """Base class for candidate parsing, validation, and execution failures."""


class CandidateParseError(VerificationError):
    """Raised when a candidate cannot be converted into an expression AST."""


class CandidateRuntimeError(VerificationError):
    """Raised when a syntactically valid expression cannot be executed."""


# These strings are part of the experiment artifact schema.  Keep them stable so
# failure rates can be aggregated across model providers and future verifier
# implementations without parsing human-readable exception messages.
FAILURE_PARSE_OR_GRAMMAR = "parse_or_grammar"
FAILURE_DEPTH = "depth"
FAILURE_NODE_COUNT = "node_count"
FAILURE_OUTPUT_BOUND = "output_bound"
FAILURE_RUNTIME = "runtime"
FAILURE_CODES = (
    FAILURE_PARSE_OR_GRAMMAR,
    FAILURE_DEPTH,
    FAILURE_NODE_COUNT,
    FAILURE_OUTPUT_BOUND,
    FAILURE_RUNTIME,
)


def _failure_code(exc: BaseException | str, *, phase: str) -> str:
    """Map a validation/execution exception to a stable category.

    The DSL owns detailed error text, but the verifier owns this classification
    boundary.  We inspect only well-defined message markers as a compatibility
    fallback for DSL implementations that expose no typed validation exceptions.
    Runtime errors are always classified as ``runtime``.  An output-bound runtime
    failure can additionally receive the more specific ``output_bound`` code in
    the caller.
    """

    text = str(exc).lower()
    if phase == "runtime":
        return FAILURE_OUTPUT_BOUND if "bound" in text and "output" in text else FAILURE_RUNTIME
    if "depth" in text:
        return FAILURE_DEPTH
    if "node count" in text or "node_count" in text or "nodes" in text:
        return FAILURE_NODE_COUNT
    if "bound" in text and ("output" in text or "value" in text):
        return FAILURE_OUTPUT_BOUND
    return FAILURE_PARSE_OR_GRAMMAR


def _add_failure(codes: list[str], code: str) -> None:
    if code not in codes:
        codes.append(code)
@dataclass(frozen=True)
class Counterexample:
    """A probe point on which a candidate disagrees with the world.

    ``inputs`` is a three-tuple in the generated world.  ``as_dict`` is convenient
    when the object is inserted into a prompt; the object itself remains immutable so
    it can safely be used to de-duplicate released feedback.
    """

    inputs: tuple[Any, ...]
    expected: Any
    predicted: Any
    index: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "inputs": tuple(self.inputs),
            "expected": self.expected,
            "predicted": self.predicted,
            **({"index": self.index} if self.index is not None else {}),
        }

    # Mapping-like aliases make the result easy to consume from a runner without
    # forcing it to know whether a dict or dataclass is being used.
    @property
    def point(self) -> tuple[Any, ...]:
        return self.inputs

    @property
    def label(self) -> Any:
        return self.expected


@dataclass
class CandidateResult:
    """All validation information for one candidate.

    ``candidate`` preserves the original model output (usually a string or a dict),
    while ``ast`` is the parsed tuple expression.  Invalid candidates still produce a
    result object; this is important for reporting syntax/runtime failure rates.
    """

    candidate: Any
    ast: Any = None
    canonical: Any = None
    canonical_hash: str = ""
    behavior_hash: str = ""
    node_count: int = 0
    depth: int = 0
    syntax_valid: bool = False
    runtime_valid: bool = False
    probe_accuracy: float = 0.0
    correct: int = 0
    total: int = 0
    predictions: tuple[Any, ...] = ()
    failures: tuple[str, ...] = ()
    failure_types: tuple[str, ...] = ()
    failure_codes: tuple[str, ...] = ()
    counterexamples: tuple[Counterexample, ...] = ()
    behavior_vector: tuple[Any, ...] = ()

    @property
    def valid(self) -> bool:
        return self.syntax_valid and self.runtime_valid

    @property
    def score(self) -> float:
        return self.probe_accuracy

    @property
    def probe_score(self) -> float:
        return self.probe_accuracy

    @property
    def expression(self) -> Any:
        return self.ast

    @property
    def hypothesis(self) -> Any:
        return self.candidate

    @property
    def codes(self) -> tuple[str, ...]:
        """Short alias for consumers that call the field ``codes``."""

        return self.failure_codes or self.failure_types

    @property
    def failure_categories(self) -> tuple[str, ...]:
        return self.failure_types or self.failure_codes

    def rank_key(self) -> tuple[Any, ...]:
        """Frozen ranking key: probe accuracy, compactness, canonical hash."""

        return (-self.probe_accuracy, self.node_count or 10**9, self.canonical_hash)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate,
            "ast": self.ast,
            "canonical": self.canonical,
            "canonical_hash": self.canonical_hash,
            "behavior_hash": self.behavior_hash,
            "node_count": self.node_count,
            "depth": self.depth,
            "syntax_valid": self.syntax_valid,
            "runtime_valid": self.runtime_valid,
            "probe_accuracy": self.probe_accuracy,
            "correct": self.correct,
            "total": self.total,
            "predictions": self.predictions,
            "failures": self.failures,
            "failure_types": self.failure_types or self.failure_codes,
            "failure_codes": self.failure_codes or self.failure_types,
            "counterexamples": tuple(item.as_dict() for item in self.counterexamples),
        }


# A descriptive alias used by some runners.
VerificationResult = CandidateResult


_TOKEN_RE = re.compile(r"\(|\)|[^\s()]+")
_OPERATORS = {"add", "sub", "mul", "neg", "ite", "gt", "eq", "var", "const"}


def _dsl_module() -> Any:
    try:
        from . import dsl  # type: ignore

        return dsl
    except (ImportError, ModuleNotFoundError):
        try:
            import dsl  # type: ignore

            return dsl
        except (ImportError, ModuleNotFoundError):
            return None


def _fallback_parse(text: str) -> tuple[Any, ...]:
    """Parse the tiny S-expression grammar when ``src.dsl`` is unavailable."""

    tokens = _TOKEN_RE.findall(text)
    if not tokens:
        raise CandidateParseError("empty expression")
    position = 0

    def parse_node() -> Any:
        nonlocal position
        if position >= len(tokens) or tokens[position] != "(":
            raise CandidateParseError("expected '('")
        position += 1
        if position >= len(tokens):
            raise CandidateParseError("missing operator")
        op = tokens[position]
        position += 1
        if op not in _OPERATORS:
            raise CandidateParseError(f"unknown operator: {op}")
        if op == "var":
            if position >= len(tokens):
                raise CandidateParseError("missing variable")
            name = tokens[position]
            position += 1
            if name not in {"x1", "x2", "x3"}:
                raise CandidateParseError(f"unknown variable: {name}")
            node = (op, name)
        elif op == "const":
            if position >= len(tokens):
                raise CandidateParseError("missing constant")
            try:
                value = int(tokens[position])
            except ValueError as exc:
                raise CandidateParseError("constant must be an integer") from exc
            position += 1
            node = (op, value)
        elif op == "neg":
            node = (op, parse_node())
        else:
            arity = 3 if op == "ite" else 2
            children = tuple(parse_node() for _ in range(arity))
            node = (op, *children)
        if position >= len(tokens) or tokens[position] != ")":
            raise CandidateParseError("missing ')'")
        position += 1
        return node

    result = parse_node()
    if position != len(tokens):
        raise CandidateParseError("trailing tokens")
    return result


def _unwrap_candidate(candidate: Any) -> Any:
    if isinstance(candidate, Mapping):
        for key in ("expression", "expr", "ast", "rule", "candidate", "hypothesis"):
            if key in candidate:
                return _unwrap_candidate(candidate[key])
    for attr in ("expression", "expr", "ast", "rule", "hypothesis"):
        if hasattr(candidate, attr) and not isinstance(candidate, (str, bytes)):
            value = getattr(candidate, attr)
            if value is not candidate:
                return _unwrap_candidate(value)
    return candidate


def parse_candidate(candidate: Any) -> Any:
    """Convert model output to the tuple AST expected by ``src.dsl``.

    Accepted forms are an S-expression string, ``{"expression": ...}``, or an
    already parsed tuple AST.  A JSON string containing the former object is also
    accepted because it is a common schema-constrained model response.
    """

    value = _unwrap_candidate(candidate)
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise CandidateParseError("empty candidate")
        if text.startswith("{"):
            try:
                return parse_candidate(json.loads(text))
            except json.JSONDecodeError as exc:
                raise CandidateParseError("invalid JSON candidate") from exc
        dsl = _dsl_module()
        parser = getattr(dsl, "parse_sexpr", None) if dsl else None
        if parser:
            try:
                return parser(text)
            except Exception as exc:  # normalize the DSL's parser exception
                raise CandidateParseError(str(exc)) from exc
        return _fallback_parse(text)
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        # JSON-decoded ASTs use lists.  Only nested node objects need conversion;
        # the operator and variable strings are atoms, not standalone candidates.
        def as_node(item: Any) -> Any:
            if isinstance(item, dict):
                return parse_candidate(item)
            if isinstance(item, list):
                return tuple(as_node(child) for child in item)
            return item

        return tuple(as_node(item) for item in value)
    return value


def _canonicalize(ast: Any) -> Any:
    dsl = _dsl_module()
    fn = getattr(dsl, "canonicalize", None) if dsl else None
    if fn:
        try:
            return fn(ast)
        except Exception:
            pass
    if not isinstance(ast, (tuple, list)) or not ast:
        return ast
    op = ast[0]
    children = tuple(_canonicalize(item) for item in ast[1:])
    if op in {"add", "mul"}:
        children = tuple(sorted(children, key=lambda item: repr(item)))
    return (op, *children)


def _to_sexpr(ast: Any) -> str:
    dsl = _dsl_module()
    fn = getattr(dsl, "to_sexpr", None) if dsl else None
    if fn:
        try:
            return fn(ast)
        except Exception:
            pass
    if not isinstance(ast, (tuple, list)) or not ast:
        return str(ast)
    op = ast[0]
    if op in {"var", "const"}:
        return f"({op} {ast[1]})"
    return "(" + " ".join([str(op), *(_to_sexpr(item) for item in ast[1:])]) + ")"


def canonical_expression(candidate: Any) -> Any:
    return _canonicalize(parse_candidate(candidate))


def canonical_hash(candidate: Any) -> str:
    text = _to_sexpr(canonical_expression(candidate))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fallback_depth(ast: Any) -> int:
    if not isinstance(ast, (tuple, list)) or len(ast) <= 1:
        return 1
    return 1 + max((_fallback_depth(child) for child in ast[1:]), default=0)


def _fallback_node_count(ast: Any) -> int:
    if not isinstance(ast, (tuple, list)):
        return 1
    return 1 + sum(_fallback_node_count(child) for child in ast[1:])


def _depth(ast: Any) -> int:
    dsl = _dsl_module()
    fn = getattr(dsl, "depth", None) if dsl else None
    if fn:
        try:
            return int(fn(ast))
        except Exception:
            pass
    return _fallback_depth(ast)


def _node_count(ast: Any) -> int:
    dsl = _dsl_module()
    fn = getattr(dsl, "node_count", None) if dsl else None
    if fn:
        try:
            return int(fn(ast))
        except Exception:
            pass
    return _fallback_node_count(ast)


def _validate_fallback(ast: Any, max_depth: int, max_nodes: int) -> None:
    if not isinstance(ast, (tuple, list)) or not ast:
        raise VerificationError("expression must be a non-empty tuple AST")
    op = ast[0]
    if op not in _OPERATORS:
        raise VerificationError(f"unknown operator: {op}")
    arity = {"var": 1, "const": 1, "neg": 1, "add": 2, "sub": 2, "mul": 2, "ite": 3, "gt": 2, "eq": 2}[op]
    if len(ast) != arity + 1:
        raise VerificationError(f"{op} expects {arity} argument(s)")
    if op == "var" and ast[1] not in {"x1", "x2", "x3"}:
        raise VerificationError(f"unknown variable: {ast[1]}")
    if op == "const" and (not isinstance(ast[1], int) or isinstance(ast[1], bool)):
        raise VerificationError("constant must be an integer")
    if _depth(ast) > max_depth:
        raise VerificationError(f"depth exceeds {max_depth}")
    if _node_count(ast) > max_nodes:
        raise VerificationError(f"node count exceeds {max_nodes}")


def validate_candidate(candidate: Any, max_depth: int = 5, max_nodes: int = 31, check_bounds: bool = True) -> Any:
    """Parse and validate a candidate, returning its canonical AST."""

    ast = parse_candidate(candidate)
    dsl = _dsl_module()
    validator = getattr(dsl, "validate_expr", None) if dsl else None
    if validator:
        try:
            if check_bounds:
                validator(ast, max_depth=max_depth, max_nodes=max_nodes, output_bound=100)
            else:
                validator(ast, max_depth=max_depth, max_nodes=max_nodes, output_bound=None)
        except TypeError:
            # A compatible implementation may use ``check_bounds`` instead of
            # ``output_bound``.  Retry that spelling before falling back to its
            # defaults.
            try:
                validator(ast, max_depth=max_depth, max_nodes=max_nodes, check_bounds=check_bounds)
            except TypeError:
                validator(ast, max_depth=max_depth, max_nodes=max_nodes)
    else:
        _validate_fallback(ast, max_depth=max_depth, max_nodes=max_nodes)
    return _canonicalize(ast)


def _fallback_evaluate(ast: Any, inputs: Any) -> int:
    if isinstance(inputs, Mapping):
        env = inputs
    else:
        env = {f"x{i + 1}": value for i, value in enumerate(inputs)}

    def ev(node: Any) -> Any:
        op = node[0]
        if op == "var":
            return env[node[1]]
        if op == "const":
            return node[1]
        if op == "neg":
            return -ev(node[1])
        if op == "add":
            return ev(node[1]) + ev(node[2])
        if op == "sub":
            return ev(node[1]) - ev(node[2])
        if op == "mul":
            return ev(node[1]) * ev(node[2])
        if op == "gt":
            return int(ev(node[1]) > ev(node[2]))
        if op == "eq":
            return int(ev(node[1]) == ev(node[2]))
        if op == "ite":
            return ev(node[2]) if ev(node[1]) else ev(node[3])
        raise CandidateRuntimeError(f"unknown operator: {op}")

    result = ev(ast)
    if isinstance(result, bool):
        result = int(result)
    if not isinstance(result, int):
        raise CandidateRuntimeError("expression did not return an integer")
    return result


def evaluate_candidate(candidate: Any, inputs: Any, output_bound: Optional[int] = 100) -> int:
    ast = parse_candidate(candidate)
    dsl = _dsl_module()
    evaluator = getattr(dsl, "evaluate", None) if dsl else None
    try:
        result = evaluator(ast, inputs) if evaluator else _fallback_evaluate(ast, inputs)
    except Exception as exc:
        if isinstance(exc, CandidateRuntimeError):
            raise
        raise CandidateRuntimeError(str(exc)) from exc
    if isinstance(result, bool):
        result = int(result)
    if not isinstance(result, int):
        raise CandidateRuntimeError("expression did not return an integer")
    if output_bound is not None and abs(result) > output_bound:
        raise CandidateRuntimeError(f"output exceeds bound {output_bound}")
    return result


def _point_parts(point: Any) -> tuple[Any, Any]:
    """Extract ``(inputs, label)`` from Example, mapping, or tuple forms."""

    if isinstance(point, Mapping):
        inputs = next((point[key] for key in ("point", "inputs", "x", "features") if key in point), None)
        label = next((point[key] for key in ("label", "y", "output", "target") if key in point), None)
        if inputs is not None and label is not None:
            return inputs, label
    for input_attr in ("point", "inputs", "x", "features"):
        if hasattr(point, input_attr):
            inputs = getattr(point, input_attr)
            for label_attr in ("label", "y", "output", "target"):
                if hasattr(point, label_attr):
                    return inputs, getattr(point, label_attr)
    if isinstance(point, (tuple, list)) and len(point) == 2:
        return point[0], point[1]
    raise ValueError(f"cannot extract labelled point from {point!r}")


def _points_for(world_or_points: Any, split: str = "probe") -> Sequence[Any]:
    if world_or_points is None:
        return ()
    if isinstance(world_or_points, Mapping):
        names = {
            "train": ("train", "X_train", "x_train", "train_points"),
            "probe": ("probe", "X_probe", "x_probe", "probe_points"),
            "test": ("test", "X_test", "x_test", "test_points"),
        }.get(split, (split,))
        for name in names:
            if name in world_or_points:
                return world_or_points[name]
    names = {
        "train": ("train", "X_train", "x_train", "train_points"),
        "probe": ("probe", "X_probe", "x_probe", "probe_points"),
        "test": ("test", "X_test", "x_test", "test_points"),
    }.get(split, (split,))
    for name in names:
        if hasattr(world_or_points, name):
            return getattr(world_or_points, name)
    # A plain sequence is interpreted as points for the requested split.
    if isinstance(world_or_points, Sequence) and not isinstance(world_or_points, (str, bytes)):
        return world_or_points
    raise ValueError(f"world has no {split} points")


def _input_signature(inputs: Any) -> tuple[Any, ...]:
    if isinstance(inputs, Mapping):
        return tuple(inputs.get(name) for name in ("x1", "x2", "x3"))
    return tuple(inputs)


class Verifier:
    """Programmatic probe/test verifier bound optionally to one synthetic world."""

    def __init__(self, world: Any = None, *, max_depth: int = 5, max_nodes: int = 31, output_bound: Optional[int] = 100, counterexample_limit: int = 2):
        self.world = world
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.output_bound = output_bound
        self.counterexample_limit = counterexample_limit

    def verify(self, candidate: Any, points: Any = None, *, split: str = "probe", counterexample_limit: Optional[int] = None) -> CandidateResult:
        selected_points = _points_for(self.world if points is None else points, split) if (self.world is not None or points is not None) else ()
        failures: list[str] = []
        failure_codes: list[str] = []
        # Parse and validation are deliberately separate phases.  This prevents a
        # caller's pre-parser (for example runner._extract_candidate) from erasing
        # the distinction between malformed output and an overlarge valid AST.
        try:
            ast = parse_candidate(candidate)
        except Exception as exc:
            failures.append(str(exc))
            _add_failure(failure_codes, _failure_code(exc, phase="parse"))
            return CandidateResult(
                candidate=candidate,
                failures=tuple(failures),
                failure_types=tuple(failure_codes),
                failure_codes=tuple(failure_codes),
            )
        try:
            ast = validate_candidate(ast, max_depth=self.max_depth, max_nodes=self.max_nodes, check_bounds=self.output_bound is not None)
            syntax_valid = True
            canonical = _canonicalize(ast)
            can_hash = canonical_hash(ast)
            node_count = _node_count(ast)
            depth = _depth(ast)
        except Exception as exc:
            failures.append(str(exc))
            _add_failure(failure_codes, _failure_code(exc, phase="validate"))
            return CandidateResult(
                candidate=candidate,
                ast=ast,
                failures=tuple(failures),
                failure_types=tuple(failure_codes),
                failure_codes=tuple(failure_codes),
            )

        predictions: list[Any] = []
        cex: list[Counterexample] = []
        runtime_valid = True
        correct = 0
        for index, point in enumerate(selected_points):
            try:
                inputs, expected = _point_parts(point)
                predicted = evaluate_candidate(ast, inputs, self.output_bound)
                predictions.append(predicted)
                if predicted == expected:
                    correct += 1
                elif len(cex) < (self.counterexample_limit if counterexample_limit is None else counterexample_limit):
                    cex.append(Counterexample(_input_signature(inputs), expected, predicted, index))
            except Exception as exc:
                runtime_valid = False
                failures.append(str(exc))
                # Every execution exception is a runtime failure.  Preserve a
                # more specific output-bound marker as a second code when the
                # evaluator enforces that constraint itself.
                _add_failure(failure_codes, FAILURE_RUNTIME)
                specific = _failure_code(exc, phase="runtime")
                if specific != FAILURE_RUNTIME:
                    _add_failure(failure_codes, specific)
                predictions.append(None)
                if len(cex) < (self.counterexample_limit if counterexample_limit is None else counterexample_limit):
                    try:
                        inputs, expected = _point_parts(point)
                        cex.append(Counterexample(_input_signature(inputs), expected, None, index))
                    except Exception:
                        pass
        total = len(selected_points)
        accuracy = correct / total if total and runtime_valid else 0.0
        behavior = tuple(predictions)
        try:
            dsl = _dsl_module()
            behavior_fn = getattr(dsl, "behavior_hash", None) if dsl else None
            domain = getattr(self.world, "domain", None)
            if behavior_fn is None:
                raise AttributeError("DSL behavior_hash unavailable")
            bhash = (
                str(behavior_fn(ast, domain))
                if domain is not None
                else str(behavior_fn(ast))
            )
        except Exception:
            bhash = (
                hashlib.sha256(repr(behavior).encode("utf-8")).hexdigest()
                if behavior
                else ""
            )
        return CandidateResult(
            candidate=candidate,
            ast=ast,
            canonical=canonical,
            canonical_hash=can_hash,
            behavior_hash=bhash,
            node_count=node_count,
            depth=depth,
            syntax_valid=syntax_valid,
            runtime_valid=runtime_valid,
            probe_accuracy=accuracy,
            correct=correct,
            total=total,
            predictions=tuple(predictions),
            failures=tuple(failures),
            failure_types=tuple(failure_codes),
            failure_codes=tuple(failure_codes),
            counterexamples=tuple(cex),
            behavior_vector=behavior,
        )

    def verify_probe(self, candidate: Any, points: Any = None, **kwargs: Any) -> CandidateResult:
        return self.verify(candidate, points, split="probe", **kwargs)

    def verify_test(self, candidate: Any, points: Any = None, **kwargs: Any) -> CandidateResult:
        return self.verify(candidate, points, split="test", **kwargs)

    # Compatibility names used by runner adapters and small notebooks.
    def evaluate(self, candidate: Any, points: Any = None, *, split: str = "probe", **kwargs: Any) -> CandidateResult:
        return self.verify(candidate, points, split=split, **kwargs)

    def score(self, candidate: Any, points: Any = None, *, split: str = "probe", **kwargs: Any) -> float:
        return self.verify(candidate, points, split=split, **kwargs).probe_accuracy

    def probe_accuracy(self, candidate: Any, points: Any = None) -> float:
        return self.verify_probe(candidate, points).probe_accuracy

    def test_accuracy(self, candidate: Any, points: Any = None) -> float:
        return self.verify_test(candidate, points).probe_accuracy

    def counterexamples(self, candidate: Any, points: Any = None, *, limit: Optional[int] = None, released: Optional[Iterable[Any]] = None) -> list[Counterexample]:
        # Ask for enough mismatches to skip already released points.  A verifier
        # that truncates at two before filtering would repeatedly expose the same
        # first two counterexamples on every round.
        requested = self.counterexample_limit if limit is None else max(0, int(limit))
        released_items = tuple(released or ())
        if released_items:
            requested += len(released_items)
        result = self.verify_probe(candidate, points, counterexample_limit=requested)
        items = list(result.counterexamples)
        if released_items:
            seen = {_counterexample_key(item) for item in released_items}
            items = [item for item in items if _counterexample_key(item) not in seen]
        release_limit = self.counterexample_limit if limit is None else max(0, int(limit))
        return items[:release_limit]

    def select_best(self, candidates: Iterable[Any], points: Any = None, *, results: bool = False) -> Any:
        verified = [item if isinstance(item, CandidateResult) else self.verify_probe(item, points) for item in candidates]
        verified = [item for item in verified if item.valid]
        if not verified:
            return None
        best = min(verified, key=lambda item: item.rank_key())
        return best if results else best.candidate

    def select_rule(self, candidates: Iterable[Any], points: Any = None, *, results: bool = False) -> Any:
        return self.select_best(candidates, points, results=results)

    def choose_rule(self, candidates: Iterable[Any], points: Any = None, *, results: bool = False) -> Any:
        return self.select_rule(candidates, points, results=results)

    def select_best_rule(self, candidates: Iterable[Any], points: Any = None, *, results: bool = False) -> Any:
        return self.select_rule(candidates, points, results=results)

    def get_counterexamples(self, candidate: Any, points: Any = None, **kwargs: Any) -> list[Counterexample]:
        return self.counterexamples(candidate, points, **kwargs)

    def archive(self, candidates: Iterable[Any], points: Any = None, *, limit: int = 4, results: bool = False) -> list[Any]:
        verified = [item if isinstance(item, CandidateResult) else self.verify_probe(item, points) for item in candidates]
        verified = [item for item in verified if item.valid]
        ranked = sorted(verified, key=lambda item: item.rank_key())
        deduplicated: list[CandidateResult] = []
        seen: set[str] = set()
        for item in ranked:
            key = item.behavior_hash or item.canonical_hash
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            deduplicated.append(item)
        ranked = deduplicated[: max(0, limit)]
        return ranked if results else [item.candidate for item in ranked]


def _counterexample_key(item: Any) -> tuple[Any, ...]:
    if isinstance(item, Counterexample):
        return item.inputs
    try:
        inputs, _ = _point_parts(item)
        return _input_signature(inputs)
    except Exception:
        return (repr(item),)


def verify_probe(candidate: Any, points: Any, **kwargs: Any) -> CandidateResult:
    return Verifier(**kwargs).verify_probe(candidate, points)


def verify_candidate(candidate: Any, points: Any, *, split: str = "probe", **kwargs: Any) -> CandidateResult:
    return Verifier(**kwargs).verify(candidate, points, split=split)


def verify(candidate: Any, points: Any, *, split: str = "probe", **kwargs: Any) -> CandidateResult:
    return verify_candidate(candidate, points, split=split, **kwargs)


def test_accuracy(candidate: Any, points: Any, **kwargs: Any) -> float:
    return Verifier(**kwargs).test_accuracy(candidate, points)


def probe_accuracy(candidate: Any, points: Any, **kwargs: Any) -> float:
    return Verifier(**kwargs).probe_accuracy(candidate, points)


def evaluate_probe(candidate: Any, points: Any, **kwargs: Any) -> float:
    return probe_accuracy(candidate, points, **kwargs)


def evaluate_test(candidate: Any, points: Any, **kwargs: Any) -> float:
    return test_accuracy(candidate, points, **kwargs)


def counterexamples(candidate: Any, points: Any, *, limit: int = 2, released: Optional[Iterable[Any]] = None, **kwargs: Any) -> list[Counterexample]:
    return Verifier(counterexample_limit=limit, **kwargs).counterexamples(candidate, points, limit=limit, released=released)


def select_rule(candidates: Iterable[Any], points: Any, *, return_result: bool = False, **kwargs: Any) -> Any:
    return Verifier(**kwargs).select_rule(candidates, points, results=return_result)


def choose_rule(candidates: Iterable[Any], points: Any, *, return_result: bool = False, **kwargs: Any) -> Any:
    return select_rule(candidates, points, return_result=return_result, **kwargs)


def select_best_rule(candidates: Iterable[Any], points: Any, *, return_result: bool = False, **kwargs: Any) -> Any:
    return select_rule(candidates, points, return_result=return_result, **kwargs)


def select_best(candidates: Iterable[Any], points: Any, *, return_result: bool = True, **kwargs: Any) -> Any:
    return Verifier(**kwargs).select_best(candidates, points, results=return_result)


def select_archive(candidates: Iterable[Any], points: Any, *, limit: int = 4, return_results: bool = False, **kwargs: Any) -> list[Any]:
    return Verifier(**kwargs).archive(candidates, points, limit=limit, results=return_results)


def get_counterexamples(candidate: Any, points: Any, *, limit: int = 2, released: Optional[Iterable[Any]] = None, **kwargs: Any) -> list[Counterexample]:
    return counterexamples(candidate, points, limit=limit, released=released, **kwargs)


def format_counterexample(item: Counterexample) -> str:
    """Render a released counterexample in the prompt's compact form."""

    return f"x={tuple(item.inputs)} -> expected {item.expected}, candidate {item.predicted}"


__all__ = [
    "FAILURE_CODES",
    "FAILURE_DEPTH",
    "FAILURE_NODE_COUNT",
    "FAILURE_OUTPUT_BOUND",
    "FAILURE_PARSE_OR_GRAMMAR",
    "FAILURE_RUNTIME",
    "CandidateParseError",
    "CandidateResult",
    "CandidateRuntimeError",
    "Counterexample",
    "VerificationError",
    "VerificationResult",
    "Verifier",
    "canonical_expression",
    "canonical_hash",
    "counterexamples",
    "choose_rule",
    "evaluate_candidate",
    "evaluate_probe",
    "evaluate_test",
    "format_counterexample",
    "get_counterexamples",
    "parse_candidate",
    "probe_accuracy",
    "select_archive",
    "select_best",
    "select_best_rule",
    "select_rule",
    "test_accuracy",
    "validate_candidate",
    "verify_probe",
    "verify",
    "verify_candidate",
]
