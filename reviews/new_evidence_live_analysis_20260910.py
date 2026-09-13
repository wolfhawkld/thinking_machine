"""Offline descriptive analysis for the frozen 27-task new-evidence run.

This module is downstream of the live adapter.  It validates the public and
private bindings, consumes only normalized response records, delegates
candidate-law scoring to :mod:`new_evidence_scoring_20260910`, and writes a
single immutable analysis artifact.  It never sends a request or opens raw
response bodies.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping


HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]
REVIEWS = HERE.parent
if str(REVIEWS) not in sys.path:
    sys.path.insert(0, str(REVIEWS))

import new_evidence_scoring_20260910 as scoring  # noqa: E402

try:  # The live adapter is supplied by the parent task.
    import new_evidence_live_20260910 as live  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover - useful while developing alone
    live = None  # type: ignore[assignment]


OUT = ROOT / "artifacts/new-evidence-live-20260910"
PLAN_PATH = OUT / "plan.json"
PRIVATE_PATH = OUT / "private.json"
GENERATION_PATH = OUT / "generation.json"
ANALYSIS_PATH = OUT / "analysis.json"
REPORT_PATH = OUT / "results.md"
BASELINES_PATH = ROOT / "artifacts/new-evidence-draft-v2-20260910/code-baselines.json"

CONDITIONS = tuple(scoring.CONDITIONS)
TASK_COUNT = 27
WORLD_COUNT = 9
MAX_CALLS = 29
BASELINE_NAMES = (
    "frozen_parent",
    "public_consistent_minnode",
    "public_consistent_nonconstant_minnode",
)
SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")


def sha(path: str | Path) -> str:
    """Return the SHA-256 digest of a file's bytes."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} is not a SHA-256 digest")
    return value


def _transport_value(transport: Any, names: Iterable[str]) -> Any:
    for name in names:
        value = getattr(transport, name, None)
        if callable(value):
            return value
    return None


def _transport_sha(transport: Any, path: str | Path) -> str:
    helper = _transport_value(transport, ("sha", "_sha"))
    return str(helper(path)) if helper is not None else sha(path)


def _transport_read_json(transport: Any, path: str | Path) -> Any:
    helper = _transport_value(transport, ("_read_json", "read_json"))
    return helper(path) if helper is not None else read_json(path)


def _transport_write_exclusive_json(transport: Any, path: str | Path, value: Mapping[str, Any]) -> None:
    helper = _transport_value(transport, ("_write_exclusive_json", "write_exclusive_json"))
    if helper is None:
        raise ValueError("live transport has no exclusive JSON writer")
    helper(path, value)


def _write_exclusive_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _validated_live() -> tuple[dict[str, Any], list[dict[str, Any]], Any]:
    if live is None:
        raise RuntimeError("new_evidence_live_20260910 is unavailable")
    result = live.validate()
    if not isinstance(result, tuple) or len(result) not in (2, 3):
        raise ValueError("live.validate() must return plan, tasks[, transport]")
    plan, tasks = result[:2]
    transport = result[2] if len(result) == 3 else getattr(live, "transport", None)
    if transport is None:
        # The current adapter exposes its proven old transport as a module
        # global.  Keeping this fallback also supports the parent adapter's
        # explicit third return value without coupling the analysis to either.
        transport = live
    if not isinstance(plan, dict) or not isinstance(tasks, list):
        raise ValueError("live.validate() returned malformed plan/tasks")
    return plan, tasks, transport


def _tasks_by_id(tasks: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    if len(tasks) != TASK_COUNT:
        raise ValueError(f"expected exactly {TASK_COUNT} public tasks")
    result: dict[str, Mapping[str, Any]] = {}
    for index, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            raise ValueError(f"public task {index} is not an object")
        task_id = task.get("task_id")
        prompt_sha = task.get("prompt_sha256")
        prompt = task.get("rendered_prompt")
        if (not isinstance(task_id, str) or not task_id or task_id in result
                or not isinstance(prompt, str) or not prompt):
            raise ValueError("public task identity/schema changed")
        if _require_sha(prompt_sha, f"task {index} prompt_sha256") != hashlib.sha256(prompt.encode("utf-8")).hexdigest():
            raise ValueError("public task prompt hash changed")
        result[task_id] = task
    return result


def _validate_generation(
    generation: Mapping[str, Any],
    plan_path: str | Path,
    tasks: list[Mapping[str, Any]],
    transport: Any,
) -> list[dict[str, Any]]:
    """Validate the normalized generation denominator and public bindings."""

    if not isinstance(generation, Mapping):
        raise ValueError("generation artifact must be a JSON object")
    expected = _tasks_by_id(tasks)
    if generation.get("plan_file_sha256") != _transport_sha(transport, plan_path):
        raise ValueError("generation plan file hash mismatch")
    if generation.get("formal_calls") != TASK_COUNT:
        raise ValueError("generation formal call count changed")
    calls = generation.get("provider_calls")
    if type(calls) is not int or not 0 <= calls <= MAX_CALLS:
        raise ValueError("generation provider call count is out of bounds")

    slots = generation.get("slots")
    if not isinstance(slots, list) or len(slots) != TASK_COUNT:
        raise ValueError(f"generation must retain exactly {TASK_COUNT} slots")
    slot_ids: set[str] = set()
    for index, slot in enumerate(slots):
        if not isinstance(slot, Mapping):
            raise ValueError(f"generation slot {index} is not an object")
        task_id = slot.get("task_id")
        if not isinstance(task_id, str) or task_id not in expected or task_id in slot_ids:
            raise ValueError("generation slots contain an unknown or duplicate task")
        if slot.get("prompt_sha256") != expected[task_id]["prompt_sha256"]:
            raise ValueError("generation slot prompt hash mismatch")
        if "index" in slot and slot["index"] != index:
            raise ValueError("generation slot index changed")
        status = slot.get("status")
        if status is not None and status not in {"response", "technical_failure", "not_dispatched"}:
            raise ValueError("generation slot status is malformed")
        slot_ids.add(task_id)
    if slot_ids != set(expected):
        raise ValueError("generation slots are not bijective with public tasks")

    records = generation.get("records")
    if not isinstance(records, list):
        raise ValueError("generation records are absent")
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ValueError(f"generation record {index} is not an object")
        task_id = record.get("task_id")
        if not isinstance(task_id, str) or task_id not in expected or task_id in seen:
            raise ValueError("generation records contain an unknown or duplicate task")
        if record.get("prompt_sha256") != expected[task_id]["prompt_sha256"]:
            raise ValueError("generation record prompt hash mismatch")
        # ``record_response`` preserves the provider's raw message content.
        # Non-text content (for example a list-shaped message) is retained and
        # becomes a scorer format miss; rejecting it here would hide that
        # preregistered invalid-response outcome.
        content = record.get("content")
        valid_choice = record.get("valid_choice")
        if valid_choice is not None and type(valid_choice) is not bool:
            raise ValueError("generation record valid_choice is malformed")
        response = record.get("response")
        if response is not None and not isinstance(response, Mapping):
            raise ValueError("generation record response is malformed")
        telemetry = record.get("thinking_telemetry")
        if telemetry is not None and not isinstance(telemetry, Mapping):
            raise ValueError("generation record thinking_telemetry is malformed")
        seen.add(task_id)
    if len(seen) > TASK_COUNT:
        raise ValueError("generation record denominator changed")
    failures = generation.get("technical_failures")
    if failures is not None and not isinstance(failures, list):
        raise ValueError("generation technical_failures is malformed")
    return [dict(record) for record in records]


def validate_generation(
    generation: Mapping[str, Any],
    plan_path: str | Path,
    tasks: list[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]],
    transport: Any | None = None,
) -> list[dict[str, Any]]:
    """Public wrapper used by focused tests and downstream audits."""

    if isinstance(tasks, Mapping):
        task_rows = list(tasks.values())
    else:
        task_rows = tasks
    return _validate_generation(generation, plan_path, task_rows, transport or live)


def _tri_state_bool(record: Mapping[str, Any] | None, field: str) -> bool | None:
    if not isinstance(record, Mapping):
        return None
    telemetry = record.get("thinking_telemetry")
    if isinstance(telemetry, Mapping) and isinstance(telemetry.get(field), bool):
        return telemetry[field]
    if field == "output_truncated":
        response = record.get("response")
        if isinstance(response, Mapping) and isinstance(response.get("finish_reason"), str):
            return response["finish_reason"] == "length"
    return None


def _usage(record: Mapping[str, Any] | None) -> tuple[int | None, int | None, float | None]:
    response = record.get("response") if isinstance(record, Mapping) else None
    if not isinstance(response, Mapping):
        return None, None, None
    values: list[int | None] = []
    for key in ("input_tokens", "output_tokens"):
        value = response.get(key)
        values.append(value if type(value) is int and value >= 0 else None)
    latency = response.get("latency_ms")
    if isinstance(latency, (int, float)) and not isinstance(latency, bool) and latency >= 0:
        latency_value: float | None = float(latency)
    else:
        latency_value = None
    return values[0], values[1], latency_value


def _condition_response_metrics(
    condition: str,
    bindings: Iterable[Mapping[str, Any]],
    records_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [binding for binding in bindings if binding.get("condition") == condition]
    if len(rows) != WORLD_COUNT:
        raise ValueError("condition task denominator changed")
    input_tokens = output_tokens = 0
    latency_total = 0.0
    input_known = output_known = latency_known = 0
    finish_reasons: Counter[str] = Counter()
    models: Counter[str] = Counter()
    reasoning_true = reasoning_false = reasoning_unknown = 0
    truncated = unknown_truncated = 0
    received = 0
    valid_choice = 0
    for binding in rows:
        record = records_by_id.get(binding["task_id"])
        if record is None:
            continue
        received += 1
        valid_choice += int(record.get("valid_choice") is True)
        response = record.get("response")
        if isinstance(response, Mapping):
            value = response.get("finish_reason")
            if isinstance(value, str) and value:
                finish_reasons[value] += 1
            else:
                finish_reasons["<unknown>"] += 1
            model = response.get("provider_model")
            if isinstance(model, str) and model:
                models[model] += 1
        else:
            finish_reasons["<unknown>"] += 1
        input_value, output_value, latency_value = _usage(record)
        if input_value is not None:
            input_tokens += input_value
            input_known += 1
        if output_value is not None:
            output_tokens += output_value
            output_known += 1
        if latency_value is not None:
            latency_total += latency_value
            latency_known += 1
        reason = _tri_state_bool(record, "reasoning_content_present")
        if reason is True:
            reasoning_true += 1
        elif reason is False:
            reasoning_false += 1
        else:
            reasoning_unknown += 1
        is_truncated = _tri_state_bool(record, "output_truncated")
        if is_truncated is True:
            truncated += 1
        elif is_truncated is None:
            unknown_truncated += 1
    finish_known = sum(value for key, value in finish_reasons.items() if key != "<unknown>")
    return {
        "received_records": received,
        "valid_choice_records": valid_choice,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_token_records": input_known,
            "output_token_records": output_known,
            "latency_ms_total": latency_total,
            "latency_records": latency_known,
            "latency_ms_mean": latency_total / latency_known if latency_known else None,
            "complete": input_known == received and output_known == received,
        },
        "finish_reason_counts": dict(sorted(finish_reasons.items())),
        "finish_reasons": dict(sorted(finish_reasons.items())),
        "finish_reason_known_records": finish_known,
        "finish_reason_unknown_records": finish_reasons.get("<unknown>", 0),
        "provider_model_counts": dict(sorted(models.items())),
        "reasoning": {
            "content_present": reasoning_true,
            "content_absent": reasoning_false,
            "unknown": reasoning_unknown,
            "known_records": reasoning_true + reasoning_false,
        },
        "reasoning_content_present": reasoning_true,
        "unknown_reasoning_presence": reasoning_unknown,
        "truncated_records": truncated,
        "unknown_truncation_records": unknown_truncated,
    }


def _condition_result(
    condition: str,
    scored: Mapping[str, Any],
    bindings: list[Mapping[str, Any]],
    records_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    rows = list(scored["world_rows"])
    if len(rows) != WORLD_COUNT:
        raise ValueError("scoring world denominator changed")
    by_ordinal = {binding["ordinal"]: binding for binding in bindings if binding.get("condition") == condition}
    if set(by_ordinal) != set(range(WORLD_COUNT)):
        raise ValueError("condition/world binding changed")
    valid = sum(bool(row["valid_program"]) for row in rows)
    missing = sum(bool(row["missing"]) for row in rows)
    invalid = WORLD_COUNT - valid - missing
    if invalid < 0:
        raise ValueError("scorer response accounting does not close")
    d0_consistent = sum(bool(row["D0_consistent"]) for row in rows)
    world_rows = []
    for row in rows:
        binding = by_ordinal[row["ordinal"]]
        record = records_by_id.get(binding["task_id"])
        world_rows.append({
            **row,
            "task_id": binding["task_id"],
            "response_present": record is not None,
            "valid_choice": record.get("valid_choice") if record is not None else None,
        })
    response = _condition_response_metrics(condition, bindings, records_by_id)
    return {
        "worlds": WORLD_COUNT,
        "tasks": WORLD_COUNT,
        "test_points_per_world": scored.get("test_points_per_world", 64),
        "mean_test_accuracy": scored["mean_test_accuracy"],
        "mean_test_correct": scored["mean_test_accuracy"] * scored.get("test_points_per_world", 64),
        "valid_programs": valid,
        "missing": missing,
        "invalid": invalid,
        "D0_consistent": d0_consistent,
        "d0_consistent_worlds": d0_consistent,
        "visible_consistent": scored["visible_consistent"],
        "test_perfect": scored["test_perfect"],
        "full_domain_exact": scored["full_domain_exact"],
        "mean_consistent_test_accuracy": scored["mean_consistent_test_accuracy"],
        **response,
        "world_rows": world_rows,
    }


def _baseline_binding(plan: Mapping[str, Any], path: Path) -> str:
    candidates = (
        "code_baselines_file_sha256",
        "code_baseline_file_sha256",
        "code_baselines_sha256",
        "baseline_file_sha256",
        "baseline_sha256",
    )
    for key in candidates:
        if key in plan:
            return _require_sha(plan[key], key)
    hashes = plan.get("input_file_hashes")
    if isinstance(hashes, Mapping):
        relative = str(path.resolve().relative_to(ROOT.resolve()))
        if relative in hashes:
            return _require_sha(hashes[relative], f"input_file_hashes[{relative!r}]")
        for key, value in hashes.items():
            if isinstance(key, str) and key.endswith("code-baselines.json"):
                return _require_sha(value, f"input_file_hashes[{key!r}]")
    # The draft audit is itself frozen and binds the artifact when a live plan
    # predates the explicit convenience field.
    audit_path = path.with_name("audit.json")
    if audit_path.is_file():
        audit = read_json(audit_path)
        artifact_hashes = audit.get("artifact_sha256") if isinstance(audit, Mapping) else None
        if isinstance(artifact_hashes, Mapping) and "code-baselines.json" in artifact_hashes:
            return _require_sha(artifact_hashes["code-baselines.json"], "draft baseline artifact hash")
    raise ValueError("code-baselines.json hash is not bound by the live plan")


def _load_baselines(path: str | Path, plan: Mapping[str, Any], transport: Any) -> tuple[dict[str, Any], str]:
    target = Path(path)
    expected = _baseline_binding(plan, target)
    actual = _transport_sha(transport, target)
    if actual != expected:
        raise ValueError("frozen code-baselines.json hash mismatch")
    value = _transport_read_json(transport, target)
    if not isinstance(value, dict) or set(value) != set(BASELINE_NAMES):
        raise ValueError("exactly three frozen code baselines are required")
    for name in BASELINE_NAMES:
        row = value[name]
        if not isinstance(row, Mapping) or row.get("new_inferential_tests") is not False:
            raise ValueError(f"code baseline {name} is malformed")
        conditions = row.get("conditions")
        if not isinstance(conditions, Mapping) or set(conditions) != set(CONDITIONS):
            raise ValueError(f"code baseline {name} condition set changed")
        for condition in CONDITIONS:
            summary = conditions[condition]
            if (not isinstance(summary, Mapping) or summary.get("worlds") != WORLD_COUNT
                    or not isinstance(summary.get("mean_test_accuracy"), (int, float))):
                raise ValueError(f"code baseline {name}/{condition} is malformed")
    return value, actual


def _baseline_comparisons(
    condition_results: Mapping[str, Mapping[str, Any]],
    baselines: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in BASELINE_NAMES:
        baseline_conditions = baselines[name]["conditions"]
        rows = {}
        for condition in CONDITIONS:
            baseline_mean = float(baseline_conditions[condition]["mean_test_accuracy"])
            model_mean = float(condition_results[condition]["mean_test_accuracy"])
            rows[condition] = {
                "model_mean_test_accuracy": model_mean,
                "code_baseline_mean_test_accuracy": baseline_mean,
                "model_minus_code_baseline": model_mean - baseline_mean,
                "worlds": WORLD_COUNT,
            }
        result[name] = {"conditions": rows, "comparison_is_descriptive": True}
    return result


def _schedule_hash(tasks: list[Mapping[str, Any]]) -> str:
    payload = [
        {"task_id": task["task_id"], "prompt_sha256": task["prompt_sha256"]}
        for task in tasks
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_analysis(
    plan: Mapping[str, Any],
    private: Mapping[str, Any],
    generation: Mapping[str, Any],
    tasks: list[Mapping[str, Any]],
    plan_path: str | Path,
    generation_path: str | Path,
    baselines: Mapping[str, Any],
    baseline_sha256: str,
) -> dict[str, Any]:
    records = generation["records"]
    records_by_id = {record["task_id"]: record for record in records}
    score_records = [
        {
            "task_id": record["task_id"],
            "prompt_sha256": record["prompt_sha256"],
            "content": record.get("content"),
        }
        for record in records
    ]
    scored = scoring.aggregate(private, score_records)
    bindings = private["bindings"]
    condition_results = {
        condition: _condition_result(condition, scored["conditions"][condition], bindings, records_by_id)
        for condition in CONDITIONS
    }
    contrasts = {
        key: {"worlds": WORLD_COUNT, **value}
        for key, value in scored["contrasts"].items()
    }
    input_tokens = output_tokens = 0
    input_known = output_known = 0
    for record in records:
        input_value, output_value, _ = _usage(record)
        if input_value is not None:
            input_tokens += input_value
            input_known += 1
        if output_value is not None:
            output_tokens += output_value
            output_known += 1
    generation_usage = generation.get("known_response_usage")
    if generation_usage is not None:
        if not isinstance(generation_usage, Mapping):
            raise ValueError("generation known_response_usage is malformed")
        if "input_tokens" in generation_usage and generation_usage["input_tokens"] != input_tokens:
            raise ValueError("generation input token usage mismatch")
        if "output_tokens" in generation_usage and generation_usage["output_tokens"] != output_tokens:
            raise ValueError("generation output token usage mismatch")
    slot_status_counts: Counter[str] = Counter(
        str(slot.get("status")) for slot in generation.get("slots", [])
    )
    provider_models: Counter[str] = Counter()
    for record in records:
        response = record.get("response")
        if isinstance(response, Mapping) and isinstance(response.get("provider_model"), str):
            provider_models[response["provider_model"]] += 1
    return {
        "kind": "new_evidence_live_descriptive_analysis_v1",
        "plan_file_sha256": _transport_sha(getattr(live, "transport", live), plan_path),
        "generation_file_sha256": sha(generation_path),
        "private_file_sha256": plan["private_file_sha256"],
        "source_private_file_sha256": plan.get("source_private_file_sha256"),
        "code_baselines_file_sha256": baseline_sha256,
        "conditions": condition_results,
        "conditions_results": condition_results,
        "contrasts": contrasts,
        "worlds": WORLD_COUNT,
        "tasks_per_condition": WORLD_COUNT,
        "tasks": TASK_COUNT,
        "received_parsed_records": len(records),
        "missing_or_unparsed_tasks": TASK_COUNT - len(records),
        "technical_failure_count": len(generation.get("technical_failures", [])),
        "provider_calls": generation.get("provider_calls"),
        "slot_status_counts": dict(sorted(slot_status_counts.items())),
        "valid_response_count": sum(record.get("valid_choice") is True for record in records),
        "provider_model_counts": dict(sorted(provider_models.items())),
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_token_records": input_known,
            "output_token_records": output_known,
            "complete": input_known == len(records) and output_known == len(records),
            "generation_usage_complete": generation.get("usage_complete"),
        },
        "code_baselines": baselines,
        "model_vs_code_baselines": _baseline_comparisons(condition_results, baselines),
        "all_world_denominators_retained": True,
        "missing_invalid_scored_as_misses": True,
        "new_inferential_tests": False,
        "p_values": None,
        "statistical_independence_claimed": False,
        "equal_information_claimed": False,
        "scope": plan.get("scoring"),
        "task_schedule_sha256": _schedule_hash(tasks),
    }


def render_report(result: Mapping[str, Any]) -> str:
    """Render a concise neutral report from the JSON analysis only."""

    lines = [
        "# New-evidence live results",
        "",
        "Descriptive offline summary of the frozen 27-task candidate-law run.",
        "",
        "## Condition means",
        "",
        "| condition | mean test accuracy | D0-consistent worlds | valid programs | missing | invalid |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    conditions = result["conditions"]
    for condition in CONDITIONS:
        row = conditions[condition]
        lines.append(
            f"| {condition} | {float(row['mean_test_accuracy']):.4f} | "
            f"{row['D0_consistent']}/{WORLD_COUNT} | {row['valid_programs']} | "
            f"{row['missing']} | {row['invalid']} |"
        )
    lines.extend([
        "",
        "## Consistency and exact recovery (secondary)",
        "",
        "| condition | all visible observations consistent | 64 test points all correct | 125-point exact recovery | consistency-gated accuracy |",
        "|---|---:|---:|---:|---:|",
    ])
    for condition in CONDITIONS:
        row = conditions[condition]
        lines.append(f"| {condition} | {row['visible_consistent']}/9 | {row['test_perfect']}/9 | "
                     f"{row['full_domain_exact']}/9 | {row['mean_consistent_test_accuracy']:.4f} |")
    lines.extend([
        "",
        "## Paired world contrasts",
        "",
        "| comparison | mean accuracy delta | wins | losses | ties |",
        "|---|---:|---:|---:|---:|",
    ])
    contrast_names = (
        ("new_evidence_vs_baseline", "new_evidence − baseline"),
        ("new_evidence_vs_repeat_evidence", "new_evidence − repeat_evidence"),
        ("repeat_evidence_vs_baseline", "repeat_evidence − baseline"),
    )
    for key, label in contrast_names:
        row = result["contrasts"][key]
        lines.append(
            f"| {label} | {float(row['mean_test_accuracy_delta']):+.4f} | "
            f"{row['wins']} | {row['losses']} | {row['ties']} |"
        )
    lines.extend([
        "",
        "## Response metadata by condition",
        "",
        "| condition | input tokens | output tokens | finish reasons | reasoning present/absent/unknown |",
        "|---|---:|---:|---|---:|",
    ])
    for condition in CONDITIONS:
        row = conditions[condition]
        usage = row["usage"]
        reasoning = row["reasoning"]
        lines.append(
            f"| {condition} | {usage['input_tokens']} ({usage['input_token_records']} known) | "
            f"{usage['output_tokens']} ({usage['output_token_records']} known) | "
            f"{json.dumps(row['finish_reason_counts'], sort_keys=True)} | "
            f"{reasoning['content_present']}/{reasoning['content_absent']}/{reasoning['unknown']} |"
        )
    lines.extend(["", "## Code baseline comparisons", ""])
    lines.append("Model-minus-baseline mean accuracies are descriptive comparisons; no significance claim is made.")
    lines.append("")
    lines.append("| baseline | baseline | new_evidence | repeat_evidence |")
    lines.append("|---|---:|---:|---:|")
    for name in BASELINE_NAMES:
        row = result["model_vs_code_baselines"][name]["conditions"]
        lines.append(
            f"| {name} | {row['baseline']['model_minus_code_baseline'] if 'baseline' in row else 'n/a'} | "
            f"{row['new_evidence']['model_minus_code_baseline']:+.4f} | "
            f"{row['repeat_evidence']['model_minus_code_baseline']:+.4f} |"
        )
    lines.extend([
        "",
        "## Fixed scope and limitations",
        "",
        "- These are nine previously used development worlds, not independent held-out-world validation.",
        "- The treatment is one explicitly supplied true evidence label; it is not unrelated noise and this run does not measure internal entropy or RSI.",
        "- The 27 calls are three conditions on the same nine worlds; they do not create 27 independent worlds.",
        "- No K4/action-switch score, old K4 success rate, or old interaction endpoint is inherited or reported.",
        "- Missing, malformed, non-binary, or otherwise invalid candidate programs score zero on the fixed 64-point test denominator; all comparisons are exploratory descriptions without p-values.",
        "- Code baselines use the separately frozen public artifact and do not establish model significance or scientific discovery ability.",
    ])
    return "\n".join(lines) + "\n"


def _load_plan_private_generation(
    plan_path: str | Path,
    private_path: str | Path,
    generation_path: str | Path,
    transport: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    plan = _transport_read_json(transport, plan_path)
    if not isinstance(plan, dict):
        raise ValueError("live plan must be a JSON object")
    private_expected = _require_sha(plan.get("private_file_sha256"), "private_file_sha256")
    private_actual = _transport_sha(transport, private_path)
    if private_actual != private_expected:
        raise ValueError("private binding hash mismatch")
    source_private = plan.get("source_private_file_sha256")
    if source_private is not None and _require_sha(source_private, "source_private_file_sha256") != private_actual:
        raise ValueError("source/private binding hash mismatch")
    private = _transport_read_json(transport, private_path)
    if not isinstance(private, dict):
        raise ValueError("private binding artifact is malformed")
    generation = _transport_read_json(transport, generation_path)
    if not isinstance(generation, dict):
        raise ValueError("generation artifact is malformed")
    for key in ("private_file_sha256", "source_private_file_sha256"):
        if key in generation and generation[key] != private_actual:
            raise ValueError(f"generation {key} mismatch")
    return plan, private, generation


def analyze(
    plan_path: str | Path = PLAN_PATH,
    private_path: str | Path = PRIVATE_PATH,
    generation_path: str | Path = GENERATION_PATH,
    analysis_path: str | Path = ANALYSIS_PATH,
    *,
    baseline_path: str | Path = BASELINES_PATH,
    report_path: str | Path = REPORT_PATH,
    write: bool = True,
) -> dict[str, Any]:
    plan, tasks, transport = _validated_live()
    # Read the plan from the transport path and compare its bound public task
    # list to validate(); this prevents a caller from analyzing another file
    # while retaining the live adapter's validation result.
    file_plan, private, generation = _load_plan_private_generation(
        plan_path, private_path, generation_path, transport
    )
    if file_plan != plan:
        raise ValueError("live validated plan differs from plan artifact")
    _tasks_by_id(tasks)
    records = _validate_generation(generation, plan_path, tasks, transport)
    generation = dict(generation)
    generation["records"] = records
    baselines, baseline_hash = _load_baselines(baseline_path, plan, transport)
    result = build_analysis(
        plan, private, generation, tasks, plan_path, generation_path, baselines, baseline_hash
    )
    if write:
        _transport_write_exclusive_json(transport, analysis_path, result)
        _write_exclusive_text(report_path, render_report(result))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="recompute and compare existing artifacts")
    args = parser.parse_args(argv)
    result = analyze(write=False)
    if args.verify:
        transport = getattr(live, "transport", live)
        saved = _transport_read_json(transport, ANALYSIS_PATH)
        if saved != result:
            raise ValueError("analysis.json does not match bound inputs")
        if REPORT_PATH.read_text(encoding="utf-8") != render_report(result):
            raise ValueError("results.md does not match bound inputs")
        print(json.dumps({"verified": True, "provider_calls": 0}, sort_keys=True))
    else:
        # analyze(write=False) above is intentional: --verify is read-only,
        # while this branch performs the two exclusive initial writes exactly
        # once after all validation and scoring has completed.
        _transport_write_exclusive_json(getattr(live, "transport", live), ANALYSIS_PATH, result)
        _write_exclusive_text(REPORT_PATH, render_report(result))
        print(json.dumps({"analysis": str(ANALYSIS_PATH), "provider_calls": 0,
                          "records": result["received_parsed_records"],
                          "missing": result["missing_or_unparsed_tasks"]}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
