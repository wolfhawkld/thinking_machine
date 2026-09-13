"""Read-only terminal replay audit for the new-evidence live run.

The live runner deliberately reuses the old one-shot transport for physical
attempt bookkeeping.  This module audits that bookkeeping without sending a
request and, for successful HTTP responses, replays the raw payload through
``new_evidence_live_20260910.record_response``.  In particular, it never
reconstructs a result through the historical opaque-option parser.

Running the module without arguments is read-only.  ``--save`` creates one
exclusive ``response-audit.json`` artifact after the same read-only checks.
"""

from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
import stat
import sys
from typing import Any, Mapping

# The live module imports the other review modules by their short filenames;
# make command-line and ``python -m unittest`` invocation resolve that stable
# review-local import path alike.
REVIEWS = Path(__file__).resolve().parent
if str(REVIEWS) not in sys.path:
    sys.path.insert(0, str(REVIEWS))

import new_evidence_live_20260910 as live


TASK_COUNT = 27
MAX_CALLS = 29
MAX_TECHNICAL_RETRIES = 2
_EVENTS = frozenset(
    {
        "start",
        "submitted",
        "request_started",
        "attempt_finished",
        "dispatch_stopped",
        "retry_scheduled",
        "complete",
    }
)
_ATTEMPT_ARTIFACT_FIELDS = frozenset(
    {"index", "task_id", "attempt", "status", "record", "failure", "raw_response_file"}
)
_RAW_PREFIX = "raw-response-"


def _read_json(path: str | Path) -> Any:
    target = Path(path)
    return json.loads(target.read_text(encoding="utf-8"))


def _sha(path: str | Path) -> str:
    return live.transport.sha(path)


def _require_int(value: Any, label: str, *, minimum: int | None = None,
                 maximum: int | None = None) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} is below its lower bound")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} is above its upper bound")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")
    return value


def _safe_regular_file(path: Path, label: str) -> None:
    """Reject symlinks and non-private files before reading an artifact."""

    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is not a regular file")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise ValueError(f"{label} must have mode 0600")


def _safe_output_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("live output directory is not a real directory")
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise ValueError("live output directory must have mode 0700")


def _safe_output_child(out: Path, name: Any, label: str) -> Path:
    """Resolve one private output basename without permitting traversal."""

    if not isinstance(name, str) or not name or Path(name).is_absolute() or Path(name).name != name:
        raise ValueError(f"{label} is not a safe output basename")
    target = out / name
    out_resolved = out.resolve()
    # ``resolve`` also catches a symlink that points outside the output tree.
    if target.resolve().parent != out_resolved:
        raise ValueError(f"{label} escapes the output directory")
    return target


def _task_maps(tasks: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if len(tasks) != TASK_COUNT:
        raise ValueError("live validation did not return exactly 27 tasks")
    by_id: dict[str, dict[str, Any]] = {}
    ids: list[str] = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError(f"task {index} is not an object")
        if set(task) != {"task_id", "rendered_prompt", "prompt_sha256"}:
            raise ValueError(f"task {index} is not the public schema")
        task_id = task["task_id"]
        prompt = task["rendered_prompt"]
        prompt_sha = task["prompt_sha256"]
        if not isinstance(task_id, str) or not task_id.strip() or task_id in by_id:
            raise ValueError(f"task {index} has a duplicate or invalid task id")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError(f"task {index} has an invalid prompt")
        if not isinstance(prompt_sha, str) or len(prompt_sha) != 64:
            raise ValueError(f"task {index} has an invalid prompt hash")
        if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != prompt_sha:
            raise ValueError(f"task {index} prompt hash mismatch")
        by_id[task_id] = task
        ids.append(task_id)
    return by_id, ids


def _event_key(event: Mapping[str, Any]) -> tuple[int, int]:
    index = _require_int(event.get("index"), "event index", minimum=0, maximum=TASK_COUNT - 1)
    attempt = _require_int(event.get("attempt"), "event attempt", minimum=1, maximum=2)
    return index, attempt


def _validate_failure(failure: Any, label: str) -> dict[str, Any]:
    if not isinstance(failure, dict):
        raise ValueError(f"{label} is not an object")
    if not isinstance(failure.get("category"), str) or not failure["category"]:
        raise ValueError(f"{label}.category is invalid")
    _require_bool(failure.get("retryable"), f"{label}.retryable")
    _require_bool(failure.get("stop_new_dispatch"), f"{label}.stop_new_dispatch")
    return failure


def _raw_name(index: int, attempt: int) -> str:
    return f"{_RAW_PREFIX}{index:02d}-attempt-{attempt:02d}.bin"


def _attempt_name(index: int, attempt: int) -> str:
    return f"attempt-{index:02d}-{attempt:02d}.json"


def _validate_response_record(record: Any, task: Mapping[str, Any], raw_payload: Any) -> None:
    """Check the new candidate-law record's public and telemetry additions."""

    if not isinstance(record, dict):
        raise ValueError("response record is not an object")
    required = {
        "task_id",
        "prompt_sha256",
        "content",
        "valid_choice",
        "valid_choice_semantics",
        "response",
        "thinking_telemetry",
    }
    if not required.issubset(record):
        raise ValueError("new-evidence response record is missing a required field")
    if record.get("task_id") != task["task_id"] or record.get("prompt_sha256") != task["prompt_sha256"]:
        raise ValueError("response record task binding changed")
    _require_bool(record.get("valid_choice"), "record.valid_choice")
    semantics = record.get("valid_choice_semantics")
    if (
        not isinstance(semantics, str)
        or "valid binary DSL" not in semantics
        or "not correctness" not in semantics
    ):
        raise ValueError("valid_choice semantics no longer distinguish syntax from correctness")

    # The raw assistant content is intentionally retained by the new adapter;
    # compare it directly to the payload before comparing the full replay.
    if not isinstance(raw_payload, Mapping):
        raise ValueError("raw response payload is not an object")
    choices = raw_payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        raise ValueError("raw response choices are malformed")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise ValueError("raw response message is malformed")
    if record.get("content") != message.get("content"):
        raise ValueError("record content differs from the raw response")

    response = record.get("response")
    if not isinstance(response, dict):
        raise ValueError("response accounting is absent")
    for field in ("input_tokens", "output_tokens"):
        _require_int(response.get(field), f"response.{field}", minimum=0)
    latency = response.get("latency_ms")
    if isinstance(latency, bool) or not isinstance(latency, (int, float)) or not math.isfinite(float(latency)) or latency < 0:
        raise ValueError("response.latency_ms is invalid")
    for field in ("provider_model", "finish_reason"):
        if not isinstance(response.get(field), str) or not response[field].strip():
            raise ValueError(f"response.{field} is invalid")
    if type(response.get("seed_supported")) is not bool:
        raise ValueError("response.seed_supported is invalid")
    for field in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "reasoning_tokens"):
        value = response.get(field)
        if value is not None:
            _require_int(value, f"response.{field}", minimum=0)

    telemetry = record.get("thinking_telemetry")
    if not isinstance(telemetry, dict):
        raise ValueError("thinking telemetry is absent")
    _require_bool(telemetry.get("reasoning_content_present"), "thinking_telemetry.reasoning_content_present")
    _require_bool(telemetry.get("output_truncated"), "thinking_telemetry.output_truncated")
    count = telemetry.get("reasoning_character_count")
    if count is not None:
        _require_int(count, "thinking_telemetry.reasoning_character_count", minimum=0)
    digest = telemetry.get("reasoning_content_sha256")
    if digest is not None and (not isinstance(digest, str) or len(digest) != 64):
        raise ValueError("thinking_telemetry.reasoning_content_sha256 is invalid")


def _validate_raw_file(out: Path, name: Any, index: int, attempt: int) -> Path:
    expected = _raw_name(index, attempt)
    if name != expected:
        raise ValueError("raw response filename does not match its physical attempt")
    target = _safe_output_child(out, name, "raw response filename")
    _safe_regular_file(target, "raw response")
    return target


def _parse_ledger(path: Path) -> list[dict[str, Any]]:
    _safe_regular_file(path, "attempt ledger")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError("attempt ledger is empty")
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            raise ValueError(f"attempt ledger has a blank line at {line_number}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"attempt ledger line {line_number} is not JSON") from exc
        if not isinstance(value, dict) or value.get("event") not in _EVENTS:
            raise ValueError(f"unknown or malformed ledger event at line {line_number}")
        events.append(value)
    return events


def _validate_dispatch_and_retries(
    events: list[dict[str, Any]],
    starts: list[dict[str, Any]],
    finishes: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> tuple[dict[tuple[int, int], dict[str, Any]], dict[tuple[int, int], int], int]:
    """Validate physical-attempt identities and the preregistered retry order."""

    positions = {id(event): position for position, event in enumerate(events)}
    start_map: dict[tuple[int, int], dict[str, Any]] = {}
    finish_map: dict[tuple[int, int], dict[str, Any]] = {}
    finish_positions: dict[tuple[int, int], int] = {}
    for event in starts:
        key = _event_key(event)
        if key in start_map:
            raise ValueError("duplicate request_started event")
        index, _ = key
        if event.get("task_id") != tasks[index]["task_id"]:
            raise ValueError("request_started task binding changed")
        start_map[key] = event
    for event in finishes:
        key = _event_key(event)
        if key in finish_map:
            raise ValueError("duplicate attempt_finished event")
        index, _ = key
        if event.get("task_id") != tasks[index]["task_id"]:
            raise ValueError("attempt_finished task binding changed")
        status = event.get("status")
        if status not in ("response", "technical_failure"):
            raise ValueError("unknown physical attempt status")
        if status == "response":
            if event.get("failure") is not None or not isinstance(event.get("record"), dict):
                raise ValueError("response attempt has a failure or no record")
        else:
            if event.get("record") is not None:
                raise ValueError("technical failure retains a response record")
            _validate_failure(event.get("failure"), "attempt failure")
        finish_map[key] = event
        finish_positions[key] = positions[id(event)]

    if set(start_map) != set(finish_map) or len(start_map) != len(finish_map):
        raise ValueError("request/finish event bijection failed")
    for key in start_map:
        if key[1] == 2 and (key[0], 1) not in start_map:
            raise ValueError("retry started without a first attempt")

    submitted = [event for event in events if event["event"] == "submitted"]
    submitted_indices: list[int] = []
    for event in submitted:
        if event.get("phase") != "first_round":
            raise ValueError("submitted event is not first-round dispatch")
        index = _require_int(event.get("index"), "submitted index", minimum=0, maximum=TASK_COUNT - 1)
        if event.get("task_id") != tasks[index]["task_id"]:
            raise ValueError("submitted task binding changed")
        if index in submitted_indices:
            raise ValueError("duplicate first-round submission")
        submitted_indices.append(index)
    first_indices = sorted(index for index, attempt in start_map if attempt == 1)
    if sorted(submitted_indices) != first_indices:
        raise ValueError("submitted/request_started first-round set differs")

    stopped = [event for event in events if event["event"] == "dispatch_stopped"]
    if len(stopped) > 1:
        raise ValueError("multiple dispatch_stopped events")
    if stopped:
        stop = stopped[0]
        if stop.get("phase") != "first_round":
            raise ValueError("dispatch_stopped is not first-round")
        _require_int(stop.get("next_index"), "dispatch_stopped.next_index", minimum=0, maximum=TASK_COUNT)
        stop_position = positions[id(stop)]
        if any(positions[id(event)] > stop_position for event in submitted):
            raise ValueError("new first-round submissions followed dispatch_stopped")

    retry_events = [event for event in events if event["event"] == "retry_scheduled"]
    retry_indices: list[int] = []
    retry_positions: list[int] = []
    for event in retry_events:
        if _require_int(event.get("attempt"), "retry_scheduled.attempt") != 2:
            raise ValueError("retry_scheduled does not identify attempt 2")
        index = _require_int(event.get("index"), "retry_scheduled index", minimum=0, maximum=TASK_COUNT - 1)
        if event.get("task_id") != tasks[index]["task_id"]:
            raise ValueError("retry_scheduled task binding changed")
        if index in retry_indices:
            raise ValueError("duplicate retry_scheduled event")
        retry_indices.append(index)
        retry_positions.append(positions[id(event)])
    if len(retry_indices) > MAX_TECHNICAL_RETRIES:
        raise ValueError("technical retry budget exceeded")
    actual_retry_indices = sorted(index for index, attempt in start_map if attempt == 2)
    if sorted(retry_indices) != actual_retry_indices:
        raise ValueError("attempt 2 is not bijective with retry_scheduled events")

    # The old transport waits for every first-round future before scheduling
    # any retry.  A ledger position check catches a retry that races ahead of
    # an unfinished first-wave attempt even when all task IDs look correct.
    first_finish_positions = [
        position for (index, attempt), position in finish_positions.items() if attempt == 1
    ]
    if retry_positions and (not first_finish_positions or min(retry_positions) <= max(first_finish_positions)):
        raise ValueError("retry was scheduled before the first wave drained")
    for index, retry_position in zip(retry_indices, retry_positions, strict=True):
        if (index, 2) not in start_map or (index, 2) not in finish_map:
            raise ValueError("retry_scheduled has no matching attempt 2")
        start_position = positions[id(start_map[(index, 2)])]
        if start_position <= retry_position:
            raise ValueError("retry request started before it was scheduled")

    first_failures = {
        index: finish_map[(index, 1)]["failure"]
        for index in range(TASK_COUNT)
        if (index, 1) in finish_map and finish_map[(index, 1)]["status"] == "technical_failure"
    }
    eligible = sorted(
        index for index, failure in first_failures.items() if failure.get("retryable") is True
    )
    if retry_indices != sorted(retry_indices) or retry_indices != eligible[: len(retry_indices)]:
        raise ValueError("retries are not the lowest-index eligible technical failures")
    if any(
        finish_map[(index, 1)]["status"] != "technical_failure"
        or finish_map[(index, 1)]["failure"].get("retryable") is not True
        for index in retry_indices
    ):
        raise ValueError("retry was not justified by a retryable technical failure")

    first_wave_stopped = any(
        failure.get("stop_new_dispatch") is True for failure in first_failures.values()
    )
    if first_wave_stopped and retry_indices:
        raise ValueError("retry launched after a non-retryable first-wave stop")

    # If a retry itself stops new dispatch, it must be the final retry event.
    # Otherwise the old transport attempts the first two eligible failures.
    retry_stop_positions = []
    for index in retry_indices:
        event = finish_map[(index, 2)]
        if event["status"] == "technical_failure" and event["failure"].get("stop_new_dispatch") is True:
            retry_stop_positions.append(finish_positions[(index, 2)])
    if retry_stop_positions:
        stop_position = min(retry_stop_positions)
        if any(position > stop_position for position in retry_positions):
            raise ValueError("retry continued after a non-retryable retry failure")
    elif not first_wave_stopped and len(retry_indices) != min(MAX_TECHNICAL_RETRIES, len(eligible)):
        raise ValueError("eligible technical retries were silently omitted")

    # A non-retryable first-wave failure blocks recovery retries.  We do not
    # compare its finish-event position with ``submitted`` events: the frozen
    # transport sets its stop flag immediately after ``_attempt`` logs the
    # finish, so another already-in-flight dispatcher can legitimately submit
    # one more first-round task in that tiny interval.  ``dispatch_stopped``
    # above remains the authoritative boundary when it is present.

    return finish_map, finish_positions, len(retry_indices)


def _validate_generation_and_artifacts(
    *,
    out: Path,
    plan: Mapping[str, Any],
    plan_sha: str,
    tasks: list[dict[str, Any]],
    generation: Mapping[str, Any],
    finish_map: Mapping[tuple[int, int], Mapping[str, Any]],
) -> tuple[int, int, int]:
    if generation.get("kind") != "shortcut_challenge_deepseek_thinking_live":
        raise ValueError("generation kind does not retain the old transport provenance")
    if generation.get("plan_file_sha256") != plan_sha:
        raise ValueError("generation plan hash mismatch")
    if generation.get("formal_calls") != TASK_COUNT:
        raise ValueError("generation formal denominator changed")
    provider_calls = _require_int(generation.get("provider_calls"), "generation.provider_calls", minimum=0, maximum=MAX_CALLS)
    attempt_count = _require_int(generation.get("attempt_count"), "generation.attempt_count", minimum=0, maximum=MAX_CALLS)
    if provider_calls != attempt_count:
        raise ValueError("generation physical-attempt counts disagree")
    slots = generation.get("slots")
    if not isinstance(slots, list) or len(slots) != TASK_COUNT:
        raise ValueError("generation does not retain exactly 27 slots")
    records = generation.get("records")
    if not isinstance(records, list):
        raise ValueError("generation records are absent")
    failures = generation.get("technical_failures")
    if not isinstance(failures, list):
        raise ValueError("generation technical_failures are absent")
    usage = generation.get("known_response_usage")
    if not isinstance(usage, dict) or set(usage) != {"input_tokens", "output_tokens"}:
        raise ValueError("generation known_response_usage schema changed")
    for key in usage:
        _require_int(usage[key], f"generation.known_response_usage.{key}", minimum=0)

    expected_attempt_artifacts: set[str] = set()
    expected_raw_files: set[str] = set()
    terminal_records: list[dict[str, Any]] = []
    expected_failures: list[dict[str, Any]] = []
    expected_valid = 0
    known_input = known_output = 0
    response_replays = 0

    for index, task in enumerate(tasks):
        slot = slots[index]
        if not isinstance(slot, dict):
            raise ValueError(f"generation slot {index} is not an object")
        if (
            slot.get("index") != index
            or slot.get("task_id") != task["task_id"]
            or slot.get("prompt_sha256") != task["prompt_sha256"]
        ):
            raise ValueError("slot/task binding changed")
        keys = sorted((key for key in finish_map if key[0] == index), key=lambda key: key[1])
        if [attempt for _, attempt in keys] not in ([], [1], [1, 2]):
            raise ValueError("slot has an invalid attempt sequence")
        if slot.get("attempt_count") != len(keys):
            raise ValueError("slot attempt_count differs from the ledger")
        expected_raw: list[str] = []
        slot_failures: list[dict[str, Any]] = []
        for key in keys:
            attempt = key[1]
            finish = finish_map[key]
            artifact_name = _attempt_name(index, attempt)
            expected_attempt_artifacts.add(artifact_name)
            artifact_path = _safe_output_child(out, artifact_name, "attempt artifact filename")
            _safe_regular_file(artifact_path, "attempt artifact")
            artifact = _read_json(artifact_path)
            if not isinstance(artifact, dict) or set(artifact) != _ATTEMPT_ARTIFACT_FIELDS:
                raise ValueError("attempt artifact schema changed")
            expected_artifact = {
                "index": index,
                "task_id": task["task_id"],
                "attempt": attempt,
                "status": finish["status"],
                "record": finish["record"],
                "failure": finish["failure"],
                "raw_response_file": finish["raw_response_file"],
            }
            if artifact != expected_artifact:
                raise ValueError("attempt artifact differs from its ledger event")

            raw_name = finish.get("raw_response_file")
            if raw_name is not None:
                raw_path = _validate_raw_file(out, raw_name, index, attempt)
                expected_raw.append(raw_name)
                expected_raw_files.add(raw_name)
            elif finish["status"] == "response":
                raise ValueError("response attempt has no raw response file")

            if finish["status"] == "response":
                if raw_name is None:
                    raise ValueError("response attempt has no raw payload")
                raw_path = _safe_output_child(out, raw_name, "raw response filename")
                try:
                    raw_payload = json.loads(raw_path.read_bytes().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("saved response body is not JSON") from exc
                record = finish["record"]
                _validate_response_record(record, task, raw_payload)
                # This call is deliberately the new adapter, not
                # ``transport.base.record_response`` from the historical run.
                replay = live.record_response(
                    task,
                    raw_payload,
                    record["response"]["latency_ms"],
                )
                if replay != record:
                    raise ValueError("raw response replay mismatch")
                response_replays += 1
                terminal_records.append(record)
                expected_valid += int(record["valid_choice"])
                known_input += record["response"]["input_tokens"]
                known_output += record["response"]["output_tokens"]
            else:
                failure = _validate_failure(finish["failure"], "technical attempt failure")
                slot_failures.append(failure)
                expected_failures.append({"index": index, "task_id": task["task_id"], **failure})

        if slot.get("raw_response_files") != expected_raw:
            raise ValueError("slot raw response file list differs from attempts")
        if slot.get("technical_failures") != slot_failures:
            raise ValueError("slot technical failure list differs from attempts")
        if not keys:
            if slot.get("status") != "not_dispatched" or slot.get("record") is not None:
                raise ValueError("undispatched slot is not explicit")
            if slot.get("technical_failures") != [] or slot.get("raw_response_files") != []:
                raise ValueError("undispatched slot retains attempt metadata")
        else:
            final = finish_map[keys[-1]]
            if slot.get("status") != final["status"] or slot.get("record") != final["record"]:
                raise ValueError("slot does not retain the terminal attempt")

    # The transport lists records in fixed slot order, not completion order.
    if records != terminal_records:
        raise ValueError("generation records differ from terminal slots")
    if len(records) > TASK_COUNT:
        raise ValueError("generation contains too many records")
    if generation.get("technical_failures") != expected_failures:
        raise ValueError("generation technical_failures differ from terminal slots")
    if generation.get("valid_response_count") != expected_valid:
        raise ValueError("generation valid_response_count differs from records")
    if generation.get("known_response_usage") != {
        "input_tokens": known_input,
        "output_tokens": known_output,
    }:
        raise ValueError("generation known response usage differs from records")

    attempt_counts = generation.get("attempt_count_by_slot")
    if not isinstance(attempt_counts, dict) or set(attempt_counts) != {str(i) for i in range(TASK_COUNT)}:
        raise ValueError("generation attempt_count_by_slot does not preserve all slots")
    for index, slot in enumerate(slots):
        if attempt_counts[str(index)] != slot["attempt_count"]:
            raise ValueError("generation attempt_count_by_slot differs from slots")

    # Ensure no unreferenced physical artifacts are quietly ignored.
    actual_attempt_artifacts = {path.name for path in out.glob("attempt-*.json")}
    if actual_attempt_artifacts != expected_attempt_artifacts:
        raise ValueError("unreferenced or missing attempt artifact")
    actual_raw_files = {path.name for path in out.glob("raw-response-*.bin")}
    if actual_raw_files != expected_raw_files:
        raise ValueError("unreferenced or missing raw response file")

    expected_complete = all(slot.get("status") != "not_dispatched" for slot in slots)
    expected_all_responses = all(slot.get("status") == "response" for slot in slots)
    if generation.get("complete") is not expected_complete:
        raise ValueError("generation complete flag differs from slots")
    if generation.get("evaluable") is not (expected_all_responses and expected_valid == TASK_COUNT):
        raise ValueError("generation evaluable flag differs from slots")
    if generation.get("usage_complete") is not (expected_all_responses and not expected_failures):
        raise ValueError("generation usage_complete flag differs from attempts")
    if generation.get("provider_calls") != sum(slot["attempt_count"] for slot in slots):
        raise ValueError("generation provider_calls differs from slots")
    if plan.get("formal_calls") != TASK_COUNT or plan.get("maximum_calls") != MAX_CALLS:
        raise ValueError("live plan denominator changed")

    return response_replays, expected_valid, len(expected_failures)


def audit() -> dict[str, Any]:
    """Read and verify one terminal new-evidence run without provider calls."""

    # ``validate`` is the source-binding gate: it checks every frozen source
    # hash before this audit opens the live output artifacts.
    plan, tasks = live.validate()
    if not isinstance(plan, dict) or not isinstance(tasks, list):
        raise ValueError("live validation returned malformed plan/tasks")
    _task_maps(tasks)
    out = Path(live.OUT)
    plan_path = Path(live.PLAN_PATH)
    _safe_output_directory(out)
    _safe_regular_file(plan_path, "live plan")
    plan_sha = _sha(plan_path)
    generation_path = _safe_output_child(out, "generation.json", "generation filename")
    _safe_regular_file(generation_path, "generation artifact")
    generation = _read_json(generation_path)
    if not isinstance(generation, dict):
        raise ValueError("generation artifact is not an object")
    ledger_path = _safe_output_child(out, "attempts.jsonl", "ledger filename")
    events = _parse_ledger(ledger_path)
    if events[0].get("event") != "start" or events[0].get("plan_file_sha256") != plan_sha:
        raise ValueError("ledger start is not bound to this plan")
    if events[-1].get("event") != "complete":
        raise ValueError("ledger is not terminal")

    starts = [event for event in events if event["event"] == "request_started"]
    finishes = [event for event in events if event["event"] == "attempt_finished"]
    if len(starts) != len(finishes):
        raise ValueError("ledger request/finish counts differ")
    if len(starts) > MAX_CALLS:
        raise ValueError("physical attempt count exceeds 29")
    finish_map, finish_positions, retry_count = _validate_dispatch_and_retries(
        events, starts, finishes, tasks
    )
    if generation.get("provider_calls") != len(starts):
        raise ValueError("generation provider_calls differs from physical ledger count")
    complete = events[-1]
    if complete.get("provider_calls") != len(starts) or complete.get("saved_record_count") != len(generation.get("records", [])):
        raise ValueError("ledger complete counts differ from generation")
    if "complete" in complete and complete["complete"] != generation.get("complete"):
        raise ValueError("ledger complete status differs from generation")

    replay_count, valid_count, failure_count = _validate_generation_and_artifacts(
        out=out,
        plan=plan,
        plan_sha=plan_sha,
        tasks=tasks,
        generation=generation,
        finish_map=finish_map,
    )
    if replay_count != len([event for event in finishes if event["status"] == "response"]):
        raise ValueError("response replay count differs from response events")

    # Do not include analysis.json in this result: the live runner invokes the
    # audit before analysis, and a later analysis must not change a repeat
    # read-only audit hash/result.
    return {
        "kind": "new_evidence_response_replay",
        "verified": True,
        "physical_attempts": len(starts),
        "task_slots": TASK_COUNT,
        "raw_response_replays": replay_count,
        "technical_retries": retry_count,
        "technical_failure_records": failure_count,
        "valid_binary_dsl_records": valid_count,
        "source_bindings_valid": True,
        "plan_sha256": plan_sha,
        "generation_sha256": _sha(generation_path),
        "ledger_sha256": _sha(ledger_path),
        "new_provider_calls": 0,
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in ([], ["--save"]):
        raise SystemExit("Usage: new_evidence_response_audit_20260910.py [--save]")
    result = audit()
    if args == ["--save"]:
        live.transport._write_exclusive_json(Path(live.OUT) / "response-audit.json", result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI
    raise SystemExit(main())
