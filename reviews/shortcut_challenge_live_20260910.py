"""Bounded DeepSeek thinking run for the shortcut-challenge tasks.

The plan is prepared by the parent experiment.  This runner only reads its
public task list, sends each rendered prompt once in the first staggered
round, and permits at most two preregistered technical retries.  It never
loads the private scoring material.  The transport is deliberately one-shot;
retry policy lives here so every physical attempt is auditable.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
PLAN_PATH = ROOT / "artifacts/shortcut-challenge-live-20260910/plan.json"
OUT = ROOT / "artifacts/shortcut-challenge-live-20260910"

# These are the already-used 128K serial configuration and endpoint.  Keeping
# the imports bound to the old runner makes the request contract auditable and
# avoids silently drifting to a different model or provider URL.
_serial_spec = importlib.util.spec_from_file_location(
    "shortcut_challenge_deepseek_serial",
    HERE.with_name("deepseek-thinking-128k-live-20260909.py"),
)
if _serial_spec is None or _serial_spec.loader is None:  # pragma: no cover
    raise ImportError("cannot load the frozen DeepSeek serial runner")
serial = importlib.util.module_from_spec(_serial_spec)
_serial_spec.loader.exec_module(serial)
base = serial.base

from src.providers.openai_compatible import (  # noqa: E402
    HTTPStatusError,
    ResponsePayloadError,
    TransportError,
)


SETTINGS = dict(serial.SETTINGS)
ENDPOINT = base.ENDPOINT
TASK_COUNT = 18
MAX_CALLS = 20
MAX_TECHNICAL_RETRIES = 2
INTERVAL_SECONDS = 3
TIMEOUT_SECONDS = 3600
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")


def sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_exclusive_json(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _write_private_bytes(path: str | Path, body: bytes) -> None:
    """Persist one raw response without allowing it into logs or JSON."""

    if not isinstance(body, bytes):
        raise TypeError("raw response body must be bytes")
    target = Path(path)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(body)
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


def _validate_plan() -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Validate all public bindings before credentials or output are opened."""

    plan = _read_json(PLAN_PATH)
    if not isinstance(plan, dict):
        raise ValueError("challenge live plan must be a JSON object")
    if plan.get("settings") != SETTINGS:
        raise ValueError("challenge live settings drift")
    if plan.get("endpoint") != ENDPOINT:
        raise ValueError("challenge live endpoint drift")
    if plan.get("timeout_seconds") != TIMEOUT_SECONDS:
        raise ValueError("challenge live timeout drift")
    if plan.get("maximum_calls") != MAX_CALLS:
        raise ValueError("challenge live maximum_calls drift")
    if plan.get("formal_calls") != TASK_COUNT:
        raise ValueError("challenge live formal_calls drift")
    if plan.get("max_technical_retries") != MAX_TECHNICAL_RETRIES:
        raise ValueError("challenge live retry budget drift")
    if plan.get("interval_seconds") != INTERVAL_SECONDS:
        raise ValueError("challenge live interval drift")

    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != TASK_COUNT:
        raise ValueError("challenge live plan must contain exactly 18 tasks")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise ValueError(f"task {index} must be an object")
        if set(task) != {"task_id", "rendered_prompt", "prompt_sha256"}:
            raise ValueError(f"task {index} contains a non-public or missing field")
        task_id = task["task_id"]
        prompt = task["rendered_prompt"]
        prompt_sha = task["prompt_sha256"]
        if (not isinstance(task_id, str) or not task_id.strip()
                or task_id in seen):
            raise ValueError(f"task {index} has a duplicate or invalid task_id")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError(f"task {index} has an invalid rendered_prompt")
        if (not isinstance(prompt_sha, str) or not _SHA256.fullmatch(prompt_sha)
                or hashlib.sha256(prompt.encode()).hexdigest() != prompt_sha):
            raise ValueError(f"task {index} prompt hash mismatch")
        seen.add(task_id)
        normalized.append({
            "task_id": task_id,
            "rendered_prompt": prompt,
            "prompt_sha256": prompt_sha,
        })

    input_hashes = plan.get("input_file_hashes")
    if not isinstance(input_hashes, dict) or not input_hashes:
        raise ValueError("challenge live plan requires input_file_hashes")
    source_key = str(HERE.relative_to(ROOT))
    if source_key not in input_hashes:
        raise ValueError("challenge live source is absent from input_file_hashes")
    for relative, expected in input_hashes.items():
        if (not isinstance(relative, str) or not isinstance(expected, str)
                or not _SHA256.fullmatch(expected)):
            raise ValueError("malformed input_file_hashes entry")
        target = _safe_project_path(relative)
        if not target.is_file() or sha(target) != expected:
            raise ValueError("bound input file hash mismatch")
    return plan, normalized


def request_body(plan: Mapping[str, Any], task: Mapping[str, str]) -> dict[str, Any]:
    """Construct a request from the plan's public settings and one prompt."""

    return {
        **dict(plan["settings"]),
        "messages": [{"role": "user", "content": task["rendered_prompt"]}],
    }


def _safe_failure(exc: BaseException) -> dict[str, Any]:
    """Map an exception to a closed, secret-free retry classification."""

    if isinstance(exc, TransportError):
        return {
            "category": "transport_error",
            "transport_category": exc.category,
            "delivery_ambiguous": exc.delivery_ambiguous,
            "retryable": exc.retryable_physical_attempt,
            "stop_new_dispatch": False,
        }
    if isinstance(exc, HTTPStatusError):
        retryable = exc.status_code == 429 or 500 <= exc.status_code <= 599
        return {
            "category": "http_status_error",
            "status_code": exc.status_code,
            "retryable": retryable,
            "stop_new_dispatch": not retryable,
        }
    if isinstance(exc, ResponsePayloadError):
        return {
            "category": "response_payload_error",
            "retryable": False,
            "stop_new_dispatch": False,
        }
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError)):
        return {
            "category": "response_or_local_contract_error",
            "retryable": False,
            "stop_new_dispatch": False,
        }
    return {
        "category": "local_runner_error",
        "retryable": False,
        "stop_new_dispatch": True,
    }


def _attempt(
    plan: Mapping[str, Any],
    task: Mapping[str, str],
    index: int,
    attempt_number: int,
    key: str,
    ledger: Any,
    lock: threading.Lock,
) -> dict[str, Any]:
    """Perform exactly one physical call and return only safe metadata."""

    with lock:
        ledger.append({
            "event": "request_started",
            "index": index,
            "attempt": attempt_number,
            "task_id": task["task_id"],
        })
    started = time.monotonic()
    raw_name = f"raw-response-{index:02d}-attempt-{attempt_number:02d}.bin"
    raw_path = OUT / raw_name
    try:
        body = json.dumps(
            request_body(plan, task),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        response = base.UrllibHTTPTransport().post(
            url=plan["endpoint"],
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
            body=body,
            timeout=plan["timeout_seconds"],
        )
        response_body = getattr(response, "body", None)
        # Save a response body before interpreting status or content.  The
        # body is private and referenced only by filename in the audit.
        if isinstance(response_body, bytes):
            _write_private_bytes(raw_path, response_body)
        else:
            raise TypeError("HTTP transport returned a non-bytes body")
        if response.status != 200:
            raise HTTPStatusError(response.status)
        payload = json.loads(response_body.decode("utf-8"))
        record = base.record_response(
            task,
            payload,
            (time.monotonic() - started) * 1000,
        )
        outcome = {
            "index": index,
            "attempt": attempt_number,
            "status": "response",
            "record": record,
            "raw_response_file": raw_name,
            "raw_response_files": [raw_name],
            "failure": None,
            "elapsed_ms": (time.monotonic() - started) * 1000,
            "attempt_count": 1,
        }
    except Exception as exc:  # classification is deliberately closed below
        failure = _safe_failure(exc)
        outcome = {
            "index": index,
            "attempt": attempt_number,
            "status": "technical_failure",
            "record": None,
            "raw_response_file": raw_name if raw_path.exists() else None,
            "raw_response_files": [raw_name] if raw_path.exists() else [],
            "failure": failure,
            "elapsed_ms": (time.monotonic() - started) * 1000,
            "attempt_count": 1,
        }

    # Persist the per-physical-attempt result before the worker returns.  This
    # is intentionally separate from generation.json: another worker may
    # still be running when a completed attempt is already durable.
    artifact = {
        "index": index,
        "task_id": task["task_id"],
        "attempt": attempt_number,
        "status": outcome["status"],
        "record": outcome["record"],
        "failure": outcome["failure"],
        "raw_response_file": outcome["raw_response_file"],
    }
    with lock:
        _write_exclusive_json(
            OUT / f"attempt-{index:02d}-{attempt_number:02d}.json",
            artifact,
        )
        ledger.append({
            "event": "attempt_finished",
            "index": index,
            "attempt": attempt_number,
            "task_id": task["task_id"],
            "status": outcome["status"],
            "raw_response_file": outcome["raw_response_file"],
            "failure": outcome["failure"],
            "record": outcome["record"],
        })
    return outcome


class _Ledger:
    """Exclusive mode-0600 JSONL ledger with fsync on every event."""

    def __init__(self, path: Path):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(descriptor, 0o600)
        self._stream = os.fdopen(descriptor, "w", encoding="utf-8")

    def append(self, value: Mapping[str, Any]) -> None:
        self._stream.write(json.dumps(dict(value), ensure_ascii=False) + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())

    def close(self) -> None:
        if not self._stream.closed:
            self._stream.close()

    def __enter__(self) -> "_Ledger":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def _dispatch_first_round(
    plan: Mapping[str, Any],
    tasks: list[dict[str, str]],
    key: str,
    ledger: _Ledger,
    lock: threading.Lock,
) -> tuple[dict[int, dict[str, Any]], list[int], bool]:
    outcomes: dict[int, dict[str, Any]] = {}
    submitted: list[int] = []
    stop_dispatch = threading.Event()

    def worker(index: int) -> dict[str, Any]:
        outcome = _attempt(plan, tasks[index], index, 1, key, ledger, lock)
        failure = outcome.get("failure")
        if failure and failure.get("stop_new_dispatch"):
            stop_dispatch.set()
        return outcome

    with ThreadPoolExecutor(max_workers=TASK_COUNT) as pool:
        futures: list[tuple[int, Any]] = []
        for index in range(TASK_COUNT):
            if index:
                time.sleep(plan["interval_seconds"])
            if stop_dispatch.is_set():
                with lock:
                    ledger.append({
                        "event": "dispatch_stopped",
                        "phase": "first_round",
                        "next_index": index,
                    })
                break
            with lock:
                ledger.append({
                    "event": "submitted",
                    "phase": "first_round",
                    "index": index,
                    "task_id": tasks[index]["task_id"],
                })
            submitted.append(index)
            futures.append((index, pool.submit(worker, index)))
        for index, future in futures:
            outcomes[index] = future.result()
    return outcomes, submitted, stop_dispatch.is_set()


def _merge_retry(
    first: dict[str, Any],
    retry: dict[str, Any],
) -> dict[str, Any]:
    """Keep the first technical failure while making the retry terminal."""

    failures = list(first.get("technical_failures", []))
    if first.get("failure") is not None:
        failures.append(first["failure"])
    retry["technical_failures"] = failures
    retry["raw_response_files"] = (
        list(first.get("raw_response_files", []))
        + list(retry.get("raw_response_files", []))
    )
    retry["attempt_count"] = first.get("attempt_count", 1) + retry.get("attempt_count", 1)
    return retry


def run(execute: bool) -> dict[str, Any]:
    if not execute:
        raise ValueError("real calls require --execute")
    plan, tasks = _validate_plan()
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise ValueError("DEEPSEEK_API_KEY is absent")
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(OUT, 0o700)
    plan_sha = sha(PLAN_PATH)
    lock = threading.Lock()
    with _Ledger(OUT / "attempts.jsonl") as ledger:
        ledger.append({"event": "start", "plan_file_sha256": plan_sha})
        outcomes, submitted, retry_blocked = _dispatch_first_round(
            plan, tasks, key, ledger, lock
        )

        retry_candidates = [
            index for index in sorted(outcomes)
            if outcomes[index].get("status") == "technical_failure"
            and outcomes[index].get("failure", {}).get("retryable") is True
        ]
        if retry_blocked:
            retry_candidates = []
        retry_candidates = retry_candidates[:MAX_TECHNICAL_RETRIES]
        for index in retry_candidates:
            with lock:
                ledger.append({
                    "event": "retry_scheduled",
                    "index": index,
                    "attempt": 2,
                    "task_id": tasks[index]["task_id"],
                })
            retry = _attempt(plan, tasks[index], index, 2, key, ledger, lock)
            outcomes[index] = _merge_retry(outcomes[index], retry)
            if (retry.get("failure") or {}).get("stop_new_dispatch"):
                # A non-retryable protocol response during recovery (for
                # example 401/403) invalidates further new dispatches.
                break

        slots: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        technical_failures: list[dict[str, Any]] = []
        total_calls = 0
        known_input = 0
        known_output = 0
        valid_response_count = 0
        for index, task in enumerate(tasks):
            outcome = outcomes.get(index)
            if outcome is None:
                slot = {
                    "index": index,
                    "task_id": task["task_id"],
                    "prompt_sha256": task["prompt_sha256"],
                    "status": "not_dispatched",
                    "attempt_count": 0,
                    "technical_failures": [],
                    "raw_response_files": [],
                    "record": None,
                }
            else:
                failures = list(outcome.get("technical_failures", []))
                if outcome.get("failure") is not None:
                    failures.append(outcome["failure"])
                # Retry merge already carries the initial failure.  A terminal
                # retry failure is appended here exactly once.
                unique_failures = failures
                record = outcome.get("record")
                status = outcome["status"]
                raw_files = list(outcome.get("raw_response_files", []))
                slot = {
                    "index": index,
                    "task_id": task["task_id"],
                    "prompt_sha256": task["prompt_sha256"],
                    "status": status,
                    "attempt_count": outcome.get("attempt_count", 1),
                    "technical_failures": unique_failures,
                    "raw_response_files": raw_files,
                    "record": record,
                }
                if record is not None:
                    records.append(record)
                    known_input += record["response"]["input_tokens"]
                    known_output += record["response"]["output_tokens"]
                    valid_response_count += int(bool(record.get("valid_choice")))
                total_calls += slot["attempt_count"]
                for failure in unique_failures:
                    technical_failures.append({
                        "index": index,
                        "task_id": task["task_id"],
                        **failure,
                    })
            slots.append(slot)

        # The generated artifact is written even when some slots are invalid,
        # technically failed, or were not dispatched.  This preserves the
        # preregistered denominator and makes missing slots explicit.
        terminal = all(slot["status"] != "not_dispatched" for slot in slots)
        all_responses = all(slot["status"] == "response" for slot in slots)
        result: dict[str, Any] = {
            "kind": "shortcut_challenge_deepseek_thinking_live",
            "created_utc": serial.base.now(),
            "plan_file_sha256": plan_sha,
            "complete": terminal,
            "evaluable": all_responses and valid_response_count == TASK_COUNT,
            "formal_calls": TASK_COUNT,
            "provider_calls": total_calls,
            "submitted_first_round_indices": submitted,
            "records": records,
            "slots": slots,
            "attempt_count": total_calls,
            "attempt_count_by_slot": {
                str(slot["index"]): slot["attempt_count"] for slot in slots
            },
            "technical_failures": technical_failures,
            "known_response_usage": {
                "input_tokens": known_input,
                "output_tokens": known_output,
            },
            "usage_complete": all_responses and not technical_failures,
            "valid_response_count": valid_response_count,
            "retry_policy": {
                "max_technical_retries": MAX_TECHNICAL_RETRIES,
                "each_failed_slot_at_most_one_retry": True,
                "retryable": "TransportError or HTTP 429/5xx only",
                "invalid_200_not_retried": True,
            },
        }
        _write_exclusive_json(OUT / "generation.json", result)
        ledger.append({
            "event": "complete",
            "complete": terminal,
            "provider_calls": total_calls,
            "saved_record_count": len(records),
        })
    print(json.dumps({
        "complete": result["complete"],
        "provider_calls": result["provider_calls"],
        "records": len(records),
        "slots": len(slots),
        "valid_response_count": result["valid_response_count"],
    }, ensure_ascii=False, sort_keys=True), flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    run(args.execute)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI
    raise SystemExit(main())
