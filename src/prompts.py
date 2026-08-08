"""Prompt construction for the bounded symbolic hypothesis-search episode.

The prompt layer deliberately contains only public information.  In
particular, probe and test labels are never copied into the initial prompt;
probe counterexamples are added explicitly by :mod:`src.runner` after a
verification round.  Keeping this policy in one small module makes it easier
to audit the information boundary of the experiment.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
import json
from typing import Any

from .dsl import DSLValidationError, to_sexpr


DSL_SPEC = """Frozen candidate grammar:
E ::= (var x1) | (var x2) | (var x3)
   | (const -3) | (const -2) | (const -1) | (const 0)
   | (const 1) | (const 2) | (const 3)
   | (add E E) | (sub E E) | (mul E E) | (neg E) | (ite P E E)
P ::= (gt E E) | (eq E E)
Candidate constraints: maximum AST depth 5, maximum AST node count 31, and
integer outputs with absolute value at most 100. The hidden target was sampled
from laws using at least two input variables and at least two operator types,
but those two generation priors are not additional validity requirements for a
candidate."""


def _field(value: Any, *names: str, default: Any = None) -> Any:
    """Read a field from a mapping or object without imposing a world type."""

    if value is None:
        return default
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _jsonable(value: Any) -> Any:
    """Convert simple dataclass/model objects into stable JSON-able values."""

    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _jsonable(value.dict())
        except Exception:
            pass
    if hasattr(value, "__dict__") and not isinstance(value, type):
        try:
            return {
                str(key): _jsonable(item)
                for key, item in vars(value).items()
                if not str(key).startswith("_")
            }
        except Exception:
            pass
    return value


def _stable(value: Any) -> str:
    """Render an example or record deterministically for a prompt."""

    value = _jsonable(value)
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _stable_candidate(value: Any) -> str:
    """Render a parsed DSL AST as DSL, with the generic fallback unchanged.

    Valid candidates are stored in the archive as immutable tuple ASTs.  The
    generic JSON renderer would turn those tuples into nested arrays, despite
    the response schema requiring ``expression`` to be a DSL string.  Only a
    tuple accepted by the frozen DSL is treated specially; arbitrary tuples,
    lists, mappings, dataclasses, and model objects retain the existing stable
    rendering behavior.
    """

    if isinstance(value, tuple):
        try:
            return to_sexpr(value)
        except DSLValidationError:
            pass
    return _stable(value)


def _examples(world: Any) -> Sequence[Any]:
    examples = _field(world, "train_examples", "train", "observations", "examples", default=())
    if examples is None:
        return ()
    return tuple(examples)


def format_example(example: Any) -> str:
    """Format one labelled training/counterexample item.

    The world and DSL modules intentionally own the exact representation of
    an example.  This function therefore preserves their mapping fields while
    accepting ordinary tuples and dataclasses used by smoke tests.
    """

    if isinstance(example, Mapping):
        inputs = _field(example, "inputs", "input", "x", "features")
        output = _field(example, "output", "label", "y", "target")
        if inputs is not None and output is not None:
            return f"inputs={_stable(inputs)} -> output={_stable(output)}"
    if isinstance(example, (tuple, list)) and len(example) == 2:
        return f"inputs={_stable(example[0])} -> output={_stable(example[1])}"
    inputs = _field(example, "inputs", "input", "x", "features")
    output = _field(example, "output", "label", "y", "target")
    if inputs is not None and output is not None:
        return f"inputs={_stable(inputs)} -> output={_stable(output)}"
    return _stable(example)


def format_candidate(record: Any) -> str:
    """Render an archive record without exposing private test information."""

    candidate = _field(record, "candidate", "expression", "ast", default=record)
    score = _field(record, "probe_score", "score", "accuracy", default=None)
    node_count = _field(record, "node_count", "size", default=None)
    if score is None and node_count is None:
        return f"candidate={_stable_candidate(candidate)}"
    metadata: list[str] = []
    if score is not None:
        metadata.append(f"probe_score={score}")
    if node_count is not None:
        metadata.append(f"node_count={node_count}")
    return f"candidate={_stable_candidate(candidate)} ({', '.join(metadata)})"


def build_round_prompt(
    world: Any,
    *,
    round_index: int,
    archive: Iterable[Any] = (),
    counterexamples: Iterable[Any] = (),
    temperature: float | None = None,
    archive_capacity: int = 4,
) -> str:
    """Build the public prompt for one generation round.

    ``round_index`` is zero-based internally but is displayed one-based.  The
    prompt asks for exactly one bounded DSL expression and deliberately does
    not mention hidden probe/test data.
    """

    rows = [format_example(item) for item in _examples(world)]
    archive_rows = list(archive)[: max(0, archive_capacity)]
    feedback_rows = list(counterexamples)
    lines = [
        "You are a hypothesis generator in a bounded symbolic science task.",
        f"This is round {round_index + 1} of 5.",
        "Infer the integer-valued rule that explains the observed examples.",
        "Return exactly one JSON object of the form {\"expression\": \"<DSL expression>\"}.",
        "Do not include prose, markdown, rationale, or code fences.",
        "Use only the following permitted DSL grammar and constraints:",
        DSL_SPEC,
        "The evaluator will check additional private examples; do not assume that memorizing the examples is sufficient.",
        "",
        "Observed training examples:",
    ]
    lines.extend(f"- {row}" for row in rows or ("(none)",))
    if archive_rows:
        lines.extend(("", "Previously explored candidates (use as stepping stones, not as ground truth):"))
        lines.extend(f"- {format_candidate(row)}" for row in archive_rows)
    if feedback_rows:
        lines.extend(("", "Verifier feedback: counterexamples that the current search must explain:"))
        lines.extend(f"- {format_example(row)}" for row in feedback_rows)
    # ``temperature`` is intentionally accepted for call-site symmetry and
    # logging, but never rendered.  Sampling temperature is an API policy
    # variable; putting its value in the prompt would create a second treatment
    # and make the fixed-budget arms incomparable.
    lines.extend(
        (
            "",
            'Final schema reminder: "expression" MUST be a non-empty JSON string '
            "containing one DSL S-expression; it MUST NOT be a JSON array or nested "
            "JSON AST. Output exactly that one JSON object now.",
        )
    )
    return "\n".join(lines)


def build_candidate_prompt(*args: Any, **kwargs: Any) -> str:
    """Backward-compatible alias used by small generators and notebooks."""

    return build_round_prompt(*args, **kwargs)


__all__ = [
    "DSL_SPEC",
    "build_candidate_prompt",
    "build_round_prompt",
    "format_candidate",
    "format_example",
]
