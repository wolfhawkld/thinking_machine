"""Descriptive analysis for the frozen 54-task association run.

This module is deliberately downstream of the live transport.  It reads the
public plan, the copied private bindings, and the transport's normalized
``generation.json``; it never opens credentials, sends requests, or reads raw
reasoning.  The immutable offline scorer remains the sole source of the
candidate/label accounting.
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
REVIEWS = HERE.parent
if str(REVIEWS) not in sys.path:
    sys.path.insert(0, str(REVIEWS))

import context_association_materials_20260910 as materials  # noqa: E402


ROOT = materials.prior.ROOT
OUT = ROOT / "artifacts/context-association-live-20260910"
PLAN_PATH = OUT / "plan.json"
PRIVATE_PATH = OUT / "private.json"
GENERATION_PATH = OUT / "generation.json"
ANALYSIS_PATH = OUT / "analysis.json"

CONDITIONS = tuple(materials.CONDITIONS)
TASK_COUNT = 54
ORIGINAL_TASK_COUNT = 18
WORLD_COUNT = 9
MAX_CALLS = 56
SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")


def sha(path: str | Path) -> str:
    """Return the SHA-256 digest of a file's bytes."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_exclusive_json(path: str | Path, value: Mapping[str, Any]) -> None:
    """Create one private, fsynced JSON artifact and refuse replacement."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _safe_project_path(relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("input_file_hashes keys must be relative project paths")
    candidate = (ROOT / relative).resolve()
    project = ROOT.resolve()
    if candidate != project and project not in candidate.parents:
        raise ValueError("input_file_hashes path escapes project root")
    return candidate


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} is not a SHA-256 digest")
    return value


def _prompt_sha(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _validate_input_hashes(plan: Mapping[str, Any]) -> None:
    hashes = plan.get("input_file_hashes")
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("live plan requires input_file_hashes")
    for relative, expected in hashes.items():
        digest = _require_sha(expected, f"input_file_hashes[{relative!r}]")
        target = _safe_project_path(relative)
        if not target.is_file() or sha(target) != digest:
            raise ValueError(f"bound input file hash mismatch: {relative}")


def _validate_public_tasks(plan: Mapping[str, Any]) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != TASK_COUNT:
        raise ValueError(f"live plan must contain exactly {TASK_COUNT} public tasks")
    normalized: list[dict[str, str]] = []
    by_id: dict[str, dict[str, str]] = {}
    for index, task in enumerate(tasks):
        if not isinstance(task, dict) or set(task) != {"task_id", "rendered_prompt", "prompt_sha256"}:
            raise ValueError(f"task {index} is not the public task schema")
        task_id = task["task_id"]
        prompt = task["rendered_prompt"]
        prompt_sha = task["prompt_sha256"]
        if not isinstance(task_id, str) or not task_id.strip() or task_id in by_id:
            raise ValueError(f"task {index} has a duplicate or invalid task_id")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError(f"task {index} has an invalid rendered_prompt")
        if _require_sha(prompt_sha, f"task {index} prompt_sha256") != _prompt_sha(prompt):
            raise ValueError(f"task {index} prompt hash mismatch")
        row = {"task_id": task_id, "rendered_prompt": prompt, "prompt_sha256": prompt_sha}
        normalized.append(row)
        by_id[task_id] = row
    return normalized, by_id


def load_plan(plan_path: str | Path = PLAN_PATH) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, dict[str, str]]]:
    """Load and validate the public plan and every bound source hash."""

    path = Path(plan_path)
    plan = read_json(path)
    if not isinstance(plan, dict):
        raise ValueError("live plan must be a JSON object")

    # These are the preregistered denominators for this analysis.  Transport
    # settings are checked by the runner; retaining the core count checks here
    # prevents an analysis from silently switching cohorts.
    if plan.get("formal_calls") != TASK_COUNT:
        raise ValueError("live plan formal_calls drift")
    if plan.get("maximum_calls") != MAX_CALLS:
        raise ValueError("live plan maximum_calls drift")
    if plan.get("max_technical_retries") != 2:
        raise ValueError("live plan retry budget drift")
    if plan.get("conditions") != list(CONDITIONS):
        raise ValueError("live plan condition order drift")
    _require_sha(plan.get("private_file_sha256"), "private_file_sha256")
    source_private = _require_sha(
        plan.get("source_private_file_sha256"), "source_private_file_sha256"
    )
    if source_private != plan["private_file_sha256"]:
        raise ValueError("source/private copied binding mismatch")
    if "source_plan_sha256" in plan:
        _require_sha(plan["source_plan_sha256"], "source_plan_sha256")
    _validate_input_hashes(plan)
    tasks, by_id = _validate_public_tasks(plan)
    return plan, tasks, by_id


_BINDING_KEYS = {
    "task_id",
    "original_task_id",
    "condition",
    "prompt_sha256",
    "option_to_raw_action",
    "candidate_hashes_by_raw",
    "pair_ordinal",
    "arm",
    "correct_option_id",
    "cross_option_id",
}


def load_private(
    private_path: str | Path,
    plan: Mapping[str, Any],
    tasks_by_id: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    """Validate the copied private binding before passing it to ``score``."""

    path = Path(private_path)
    expected_sha = _require_sha(plan.get("private_file_sha256"), "private_file_sha256")
    if not path.is_file() or sha(path) != expected_sha:
        raise ValueError("copied private.json hash mismatch")
    private = read_json(path)
    if not isinstance(private, dict) or not isinstance(private.get("bindings"), list):
        raise ValueError("private file has no binding list")
    if len(private["bindings"]) != TASK_COUNT:
        raise ValueError("private binding count changed")
    if "source_plan_sha256" in plan and private.get("source_plan_sha256") != plan["source_plan_sha256"]:
        raise ValueError("private source plan binding changed")
    if "source_private_sha256" in private:
        _require_sha(private["source_private_sha256"], "private source_private_sha256")

    seen: set[str] = set()
    by_condition: dict[str, list[dict[str, Any]]] = {condition: [] for condition in CONDITIONS}
    by_pair_condition: dict[tuple[str, int], set[str]] = {}
    by_original: dict[str, set[str]] = {}
    for index, binding in enumerate(private["bindings"]):
        if not isinstance(binding, dict) or set(binding) != _BINDING_KEYS:
            raise ValueError(f"private binding {index} schema changed")
        task_id = binding["task_id"]
        if not isinstance(task_id, str) or task_id in seen or task_id not in tasks_by_id:
            raise ValueError(f"private binding {index} task identity changed")
        if binding["prompt_sha256"] != tasks_by_id[task_id]["prompt_sha256"]:
            raise ValueError("private/public prompt binding changed")
        condition = binding["condition"]
        if condition not in CONDITIONS:
            raise ValueError("private binding condition changed")
        ordinal = binding["pair_ordinal"]
        if type(ordinal) is not int or not 0 <= ordinal < WORLD_COUNT:
            raise ValueError("private pair ordinal changed")
        arm = binding["arm"]
        if arm not in ("context_a", "context_b"):
            raise ValueError("private arm changed")
        option_map = binding["option_to_raw_action"]
        if not isinstance(option_map, dict) or len(option_map) != 10:
            raise ValueError("private option mapping changed")
        if binding["correct_option_id"] not in option_map or binding["cross_option_id"] not in option_map:
            raise ValueError("private score option missing from mapping")
        hashes = binding["candidate_hashes_by_raw"]
        if not isinstance(hashes, list) or len(hashes) != 10 or not all(isinstance(v, str) for v in hashes):
            raise ValueError("private candidate hash mapping changed")
        seen.add(task_id)
        by_condition[condition].append(binding)
        pair_key = (condition, ordinal)
        arms = by_pair_condition.setdefault(pair_key, set())
        if arm in arms:
            raise ValueError("duplicate arm in private world")
        arms.add(arm)
        original = binding["original_task_id"]
        if not isinstance(original, str) or not original:
            raise ValueError("private original task identity changed")
        by_original.setdefault(original, set()).add(condition)

    if seen != set(tasks_by_id):
        raise ValueError("private/public task sets are not bijective")
    if len(by_original) != ORIGINAL_TASK_COUNT or any(
        conditions != set(CONDITIONS) for conditions in by_original.values()
    ):
        raise ValueError("private original-task condition blocks changed")
    for condition in CONDITIONS:
        rows = by_condition[condition]
        if len(rows) != 18 or {
            (row["pair_ordinal"], row["arm"]) for row in rows
        } != {(ordinal, arm) for ordinal in range(WORLD_COUNT) for arm in ("context_a", "context_b")}:
            raise ValueError("private condition/world denominator changed")
        if any(by_pair_condition[(condition, ordinal)] != {"context_a", "context_b"}
               for ordinal in range(WORLD_COUNT)):
            raise ValueError("private world arm denominator changed")
    return private


def validate_generation(
    generation: Mapping[str, Any],
    plan_path: str | Path,
    tasks_by_id: Mapping[str, Mapping[str, str]],
) -> list[dict[str, Any]]:
    """Validate normalized generation records without inspecting raw output."""

    if not isinstance(generation, dict):
        raise ValueError("generation artifact must be a JSON object")
    expected_plan_sha = sha(plan_path)
    if generation.get("plan_file_sha256") != expected_plan_sha:
        raise ValueError("generation plan file hash mismatch")
    if "formal_calls" in generation and generation["formal_calls"] != TASK_COUNT:
        raise ValueError("generation formal call count changed")
    calls = generation.get("provider_calls")
    if calls is not None and (type(calls) is not int or calls < 0 or calls > MAX_CALLS):
        raise ValueError("generation provider call count is out of bounds")
    records = generation.get("records")
    if not isinstance(records, list):
        raise ValueError("generation records are absent")
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"generation record {index} is not an object")
        task_id = record.get("task_id")
        if not isinstance(task_id, str) or task_id not in tasks_by_id or task_id in seen:
            raise ValueError("generation records contain an unknown or duplicate task")
        if record.get("prompt_sha256") != tasks_by_id[task_id]["prompt_sha256"]:
            raise ValueError("generation record prompt hash mismatch")
        seen.add(task_id)

    slots = generation.get("slots")
    if slots is not None:
        if not isinstance(slots, list) or len(slots) != TASK_COUNT:
            raise ValueError("generation slots do not preserve the fixed denominator")
        slot_ids: set[str] = set()
        for index, slot in enumerate(slots):
            if not isinstance(slot, dict):
                raise ValueError(f"generation slot {index} is not an object")
            task_id = slot.get("task_id")
            if not isinstance(task_id, str) or task_id not in tasks_by_id or task_id in slot_ids:
                raise ValueError("generation slots contain an unknown or duplicate task")
            if slot.get("prompt_sha256") != tasks_by_id[task_id]["prompt_sha256"]:
                raise ValueError("generation slot prompt hash mismatch")
            slot_ids.add(task_id)
        if slot_ids != set(tasks_by_id):
            raise ValueError("generation slots are not bijective with the public tasks")
    failures = generation.get("technical_failures")
    if failures is not None and not isinstance(failures, list):
        raise ValueError("generation technical_failures is malformed")
    return records


def _valid_choice(binding: Mapping[str, Any], record: Mapping[str, Any] | None) -> bool:
    return bool(
        record
        and record.get("valid_choice") is True
        and record.get("selected_option_id") in binding["option_to_raw_action"]
    )


def _tri_state_bool(record: Mapping[str, Any], field: str) -> bool | None:
    telemetry = record.get("thinking_telemetry")
    if isinstance(telemetry, Mapping) and isinstance(telemetry.get(field), bool):
        return telemetry[field]
    if field == "output_truncated":
        response = record.get("response")
        if isinstance(response, Mapping) and isinstance(response.get("finish_reason"), str):
            return response["finish_reason"] == "length"
    return None


def _usage(record: Mapping[str, Any]) -> tuple[int | None, int | None]:
    response = record.get("response")
    if not isinstance(response, Mapping):
        return None, None
    values: list[int | None] = []
    for key in ("input_tokens", "output_tokens"):
        value = response.get(key)
        values.append(value if type(value) is int and value >= 0 else None)
    return values[0], values[1]


def _condition_metrics(
    condition: str,
    scored: Mapping[str, Any],
    bindings: Iterable[Mapping[str, Any]],
    records_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [row for row in bindings if row["condition"] == condition]
    invalid = received = truncated = reasoning_present = 0
    unknown_truncated = unknown_reasoning = 0
    input_tokens = output_tokens = 0
    input_known = output_known = 0
    by_ordinal: dict[int, list[Mapping[str, Any]]] = {ordinal: [] for ordinal in range(WORLD_COUNT)}
    for binding in rows:
        record = records_by_id.get(binding["task_id"])
        if record is None:
            by_ordinal[binding["pair_ordinal"]].append({"binding": binding, "record": None})
            continue
        received += 1
        valid = _valid_choice(binding, record)
        invalid += int(not valid)
        by_ordinal[binding["pair_ordinal"]].append({"binding": binding, "record": record})
        truncated_value = _tri_state_bool(record, "output_truncated")
        if truncated_value is True:
            truncated += 1
        elif truncated_value is None:
            unknown_truncated += 1
        reasoning_value = _tri_state_bool(record, "reasoning_content_present")
        if reasoning_value is True:
            reasoning_present += 1
        elif reasoning_value is None:
            unknown_reasoning += 1
        input_value, output_value = _usage(record)
        if input_value is not None:
            input_known += 1
            input_tokens += input_value
        if output_value is not None:
            output_known += 1
            output_tokens += output_value

    world_rows = []
    base_world_rows = scored["world_rows"]
    for base_row in base_world_rows:
        ordinal = base_row["pair_ordinal"]
        world = by_ordinal[ordinal]
        world_invalid = sum(
            int(item["record"] is not None and not _valid_choice(item["binding"], item["record"]))
            for item in world
        )
        world_truncated = sum(
            int(_tri_state_bool(item["record"], "output_truncated") is True)
            for item in world
            if item["record"] is not None
        )
        world_rows.append({**base_row, "invalid": world_invalid, "truncated": world_truncated})

    usage_complete = input_known == received and output_known == received
    result = {
        "worlds": WORLD_COUNT,
        "tasks": 18,
        "own": scored["own"],
        "cross": scored["cross"],
        # ``full`` is the preregistered complete two-arm switch count.  Keep
        # the scorer's name too, so the descriptive output is unambiguous.
        "full": scored["complete_switch"],
        "complete_switch": scored["complete_switch"],
        "valid": scored["valid_arms"],
        "valid_arms": scored["valid_arms"],
        "missing": scored["missing"],
        "invalid": invalid,
        "received_records": received,
        "truncated": truncated,
        "unknown_truncation": unknown_truncated,
        "reasoning_present": reasoning_present,
        "unknown_reasoning_presence": unknown_reasoning,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_token_records": input_known,
            "output_token_records": output_known,
            "complete": usage_complete,
        },
        "world_rows": world_rows,
    }
    if result["valid"] + result["missing"] + result["invalid"] != 18:
        raise ValueError(f"{condition} response accounting does not close")
    return result


def _wins_losses_ties(left_values: Iterable[int | bool], right_values: Iterable[int | bool]) -> dict[str, int]:
    wins = losses = ties = 0
    for left, right in zip(left_values, right_values, strict=True):
        if left > right:
            wins += 1
        elif left < right:
            losses += 1
        else:
            ties += 1
    return {"wins": wins, "losses": losses, "ties": ties}


def _pairwise(
    condition_scores: Mapping[str, Mapping[str, Any]],
    left: str,
    right: str,
) -> dict[str, Any]:
    left_rows = condition_scores[left]["world_rows"]
    right_rows = condition_scores[right]["world_rows"]
    if len(left_rows) != WORLD_COUNT or len(right_rows) != WORLD_COUNT:
        raise ValueError("pairwise denominator changed")
    if [row["pair_ordinal"] for row in left_rows] != [row["pair_ordinal"] for row in right_rows]:
        raise ValueError("pairwise world alignment changed")
    own_left = [row["own"] for row in left_rows]
    own_right = [row["own"] for row in right_rows]
    full_left = [bool(row["complete_switch"]) for row in left_rows]
    full_right = [bool(row["complete_switch"]) for row in right_rows]
    own_wlt = _wins_losses_ties(own_left, own_right)
    full_wlt = _wins_losses_ties(full_left, full_right)
    rows = [
        {
            "pair_ordinal": lrow["pair_ordinal"],
            "left_own": lrow["own"],
            "right_own": rrow["own"],
            "own_delta": lrow["own"] - rrow["own"],
            "left_full": bool(lrow["complete_switch"]),
            "right_full": bool(rrow["complete_switch"]),
            "full_delta": int(bool(lrow["complete_switch"])) - int(bool(rrow["complete_switch"])),
        }
        for lrow, rrow in zip(left_rows, right_rows, strict=True)
    ]
    result = {
        "left_condition": left,
        "right_condition": right,
        "worlds": WORLD_COUNT,
        "own": {
            "left_total": sum(own_left),
            "right_total": sum(own_right),
            "delta": sum(own_left) - sum(own_right),
            **own_wlt,
        },
        "full": {
            "left_total": sum(full_left),
            "right_total": sum(full_right),
            "delta": sum(full_left) - sum(full_right),
            **full_wlt,
        },
        # Flat aliases make the fixed-world wins/losses/ties explicit to
        # downstream report readers without requiring nested-key knowledge.
        "own_wins": own_wlt["wins"],
        "own_losses": own_wlt["losses"],
        "own_ties": own_wlt["ties"],
        "full_wins": full_wlt["wins"],
        "full_losses": full_wlt["losses"],
        "full_ties": full_wlt["ties"],
        "world_rows": rows,
    }
    for key in ("wins", "losses", "ties"):
        if own_wlt[key] + 0 != own_wlt[key]:  # pragma: no cover - type guard
            raise AssertionError("non-integral own comparison")
    return result


def build_analysis(
    plan: Mapping[str, Any],
    private: Mapping[str, Any],
    generation: Mapping[str, Any],
    tasks: list[dict[str, str]],
    plan_path: str | Path,
    generation_path: str | Path,
) -> dict[str, Any]:
    records = generation["records"]
    records_by_id = {record["task_id"]: record for record in records}
    scored = materials.score(private, records)
    bindings = private["bindings"]
    condition_scores = {
        condition: _condition_metrics(condition, scored[condition], bindings, records_by_id)
        for condition in CONDITIONS
    }
    pairwise = {
        "aligned_vs_unmatched": _pairwise(condition_scores, "aligned", "unmatched"),
        "aligned_vs_no_reference": _pairwise(condition_scores, "aligned", "no_reference"),
        "unmatched_vs_no_reference": _pairwise(condition_scores, "unmatched", "no_reference"),
    }
    truncated_known = sum(
        _tri_state_bool(record, "output_truncated") is True for record in records
    )
    reasoning_known = sum(
        _tri_state_bool(record, "reasoning_content_present") is True for record in records
    )
    input_tokens = output_tokens = input_known = output_known = 0
    for record in records:
        input_value, output_value = _usage(record)
        if input_value is not None:
            input_tokens += input_value
            input_known += 1
        if output_value is not None:
            output_tokens += output_value
            output_known += 1
    generation_usage = generation.get("known_response_usage")
    if generation_usage is not None:
        if not isinstance(generation_usage, dict):
            raise ValueError("generation known_response_usage is malformed")
        for key, computed in (("input_tokens", input_tokens), ("output_tokens", output_tokens)):
            if key in generation_usage and generation_usage[key] != computed:
                raise ValueError(f"generation usage mismatch for {key}")
    failures = generation.get("technical_failures", [])
    slot_status_counts: Counter[str] = Counter()
    for slot in generation.get("slots", []):
        slot_status_counts[str(slot.get("status"))] += 1
    provider_calls = generation.get("provider_calls")
    result = {
        "kind": "context_association_live_descriptive_analysis_v1",
        "plan_file_sha256": sha(plan_path),
        "generation_file_sha256": sha(generation_path),
        "private_file_sha256": plan["private_file_sha256"],
        "source_private_file_sha256": plan["source_private_file_sha256"],
        "conditions": list(CONDITIONS),
        "worlds": WORLD_COUNT,
        "tasks_per_condition": 18,
        "tasks": TASK_COUNT,
        "received_parsed_records": len(records),
        "missing_or_unparsed_tasks": TASK_COUNT - len(records),
        "technical_failure_count": len(failures),
        "provider_calls": provider_calls,
        "slot_status_counts": dict(sorted(slot_status_counts.items())),
        "valid_response_count": sum(
            record.get("valid_choice") is True for record in records
        ),
        "truncated_records": truncated_known,
        "unknown_truncation_records": sum(
            _tri_state_bool(record, "output_truncated") is None for record in records
        ),
        "reasoning_present_records": reasoning_known,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "input_token_records": input_known,
            "output_token_records": output_known,
            "complete": input_known == len(records) and output_known == len(records),
            "generation_usage_complete": generation.get("usage_complete"),
        },
        "conditions_results": condition_scores,
        "pairwise_world_contrasts": pairwise,
        "all_world_denominators_retained": True,
        "missing_invalid_scored_as_misses": True,
        "new_inferential_tests": False,
        "p_values": None,
        "equal_information_claimed": False,
        "statistical_independence_claimed": False,
        "scope": plan.get("evidence_scope"),
        "task_schedule_sha256": hashlib.sha256(
            json.dumps(
                [{"task_id": task["task_id"], "prompt_sha256": task["prompt_sha256"]} for task in tasks],
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }
    return result


def analyze(
    plan_path: str | Path = PLAN_PATH,
    private_path: str | Path = PRIVATE_PATH,
    generation_path: str | Path = GENERATION_PATH,
    analysis_path: str | Path = ANALYSIS_PATH,
    *,
    write: bool = True,
) -> dict[str, Any]:
    plan, tasks, tasks_by_id = load_plan(plan_path)
    private = load_private(private_path, plan, tasks_by_id)
    generation = read_json(generation_path)
    records = validate_generation(generation, plan_path, tasks_by_id)
    # validate_generation returns the same list after checking it; keeping the
    # call explicit documents the generation-to-public binding before score().
    generation = dict(generation)
    generation["records"] = records
    result = build_analysis(plan, private, generation, tasks, plan_path, generation_path)
    if write:
        write_exclusive_json(analysis_path, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="recompute and compare existing analysis.json")
    args = parser.parse_args(argv)
    result = analyze(write=False)
    if args.verify:
        saved = read_json(ANALYSIS_PATH)
        if saved != result:
            raise ValueError("analysis.json does not match the bound inputs")
        print(json.dumps({"verified": True, "provider_calls": 0}, sort_keys=True))
    else:
        write_exclusive_json(ANALYSIS_PATH, result)
        print(json.dumps({
            "analysis": str(ANALYSIS_PATH),
            "provider_calls": 0,
            "records": result["received_parsed_records"],
            "missing": result["missing_or_unparsed_tasks"],
        }, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI
    raise SystemExit(main())
