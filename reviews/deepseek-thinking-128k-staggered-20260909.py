"""User-authorized migration: preserve saved responses, stagger pending calls by 3s.

Original serial attempt remains interrupted, not reclassified as a clean run.
This amended descriptive bundle includes a disclosed ambiguous prior attempt.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import threading
import time

HERE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("serial", HERE.with_name("deepseek-thinking-128k-live-20260909.py"))
serial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serial)
base = serial.base
OUT = base.ROOT / "artifacts/deepseek-thinking-128k-staggered-20260909"
INTERVAL = 3


def snapshot():
    old = serial.checked_plan()
    events = [json.loads(s) for s in (serial.OUT / "attempts.jsonl").read_text().splitlines()]
    if events[0].get("plan_file_sha256") != base.sha(serial.OUT / "plan.json"):
        raise ValueError("serial ledger plan mismatch")
    starts, records = [], {}
    for event in events[1:]:
        i = event.get("index")
        if event["event"] == "attempt_started":
            if i != len(starts) or event["task_id"] != old["tasks"][i]["task_id"]:
                raise ValueError("unexpected serial attempt order")
            starts.append(i)
        elif event["event"] == "response":
            if i not in starts or i in records:
                raise ValueError("unexpected serial response")
            record = event["record"]
            if any(record[k] != old["tasks"][i][k] for k in ("task_id", "prompt_sha256")):
                raise ValueError("serial response binding mismatch")
            records[i] = record
        else:
            raise ValueError("expected interrupted serial ledger, not failed/complete run")
    interrupted = sorted(set(starts) - set(records))
    if len(interrupted) != 1 or interrupted[0] != len(starts) - 1:
        raise ValueError("migration requires one interrupted trailing attempt")
    return old, starts, records, interrupted


def plan():
    old, starts, records, interrupted = snapshot()
    pending = [i for i in range(48) if i not in records]
    result = {"kind": "user_amended_staggered_thinking_followup", "created_utc": base.now(),
              "evidence_scope": "post_outcome_descriptive_mixed_schedule_with_disclosed_interruption",
              "authorization": "user approved interrupting in-flight request and resending unsaved tasks; potential duplicate billing disclosed",
              "settings": serial.SETTINGS, "timeout_seconds": serial.TIMEOUT, "endpoint": base.ENDPOINT,
              "interval_seconds": INTERVAL, "max_workers": 48, "task_count": 48,
              "serial_plan_file_sha256": base.sha(serial.OUT / "plan.json"),
              "serial_ledger_file_sha256": base.sha(serial.OUT / "attempts.jsonl"),
              "source_file_sha256": base.sha(HERE), "serial_source_file_sha256": base.sha(serial.HERE),
              "preserved_indices": sorted(records), "pending_indices": pending,
              "interrupted_prior_indices": interrupted, "prior_physical_attempts": len(starts),
              "maximum_new_calls": len(pending), "maximum_total_physical_attempts": len(starts) + len(pending),
              "maximum_new_completion_tokens": len(pending) * serial.SETTINGS["max_tokens"],
              "total_usage_complete": False, "interrupted_request_usage": "unknown, server may continue",
              "automatic_retry": False, "automatic_resume": False, "private_key_read": False,
              "failure_policy": "stop new dispatch on HTTP/transport/envelope failure, drain already submitted requests; retain all responses; incomplete bundle not fully scored",
              "analysis": "unchanged paired endpoints and all baselines, descriptive only; disclose mixed serial/staggered schedule and rerun of interrupted task, no primary/significance claim"}
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.save(OUT / "plan.json", result)
    print(f"Migration frozen: preserve {len(records)}, dispatch {len(pending)} at 3s intervals; interrupted indices={interrupted}.", flush=True)


def checked_plan():
    p = base.read(OUT / "plan.json")
    old, starts, records, interrupted = snapshot()
    if (p["source_file_sha256"] != base.sha(HERE)
        or p["serial_source_file_sha256"] != base.sha(serial.HERE)
        or p["serial_plan_file_sha256"] != base.sha(serial.OUT / "plan.json")
        or p["serial_ledger_file_sha256"] != base.sha(serial.OUT / "attempts.jsonl")
        or p["settings"] != serial.SETTINGS or p["timeout_seconds"] != serial.TIMEOUT
        or p["interval_seconds"] != INTERVAL or p["max_workers"] != 48
        or p["preserved_indices"] != sorted(records) or p["interrupted_prior_indices"] != interrupted
        or p["pending_indices"] != [i for i in range(48) if i not in records]):
        raise ValueError("migration input drift")
    return p, old, records


def run(execute):
    if not execute:
        raise ValueError("requires --execute")
    p, old, records = checked_plan()
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("DEEPSEEK_API_KEY absent")
    fd = os.open(OUT / "attempts.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    lock = threading.Lock()
    stop = threading.Event()
    failures, dispatched = [], []
    with os.fdopen(fd, "w") as ledger:
        def event(value):
            # Caller holds lock once worker threads exist.
            ledger.write(json.dumps({"utc": base.now(), **value}) + "\n")
            ledger.flush()
            os.fsync(ledger.fileno())
        plan_sha = base.sha(OUT / "plan.json")
        event({"event": "start", "plan_file_sha256": plan_sha, "preserved_count": len(records)})

        def worker(index):
            task = old["tasks"][index]
            with lock:
                event({"event": "attempt_started", "index": index, "task_id": task["task_id"]})
            start = time.monotonic()
            try:
                response = base.UrllibHTTPTransport().post(url=base.ENDPOINT,
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                    body=json.dumps(serial.request_body(task)).encode(), timeout=serial.TIMEOUT)
                if response.status != 200:
                    raise base.HTTPStatusError(response.status)
                record = base.record_response(task, json.loads(response.body), (time.monotonic() - start) * 1000)
                with lock:
                    base.save(OUT / f"response-{index:02d}.json", record)
                    event({"event": "response", "index": index, "record": record})
                    records[index] = record
                    print(f"saved {len(records)}/48; task {index + 1}; valid={record['valid_choice']}; finish={record['response']['finish_reason']}", flush=True)
            except Exception as exc:
                stop.set()
                failure = {"index": index, "error_type": type(exc).__name__,
                           "http_status": getattr(exc, "status_code", None), "usage_complete": False}
                with lock:
                    failures.append(failure)
                    event({"event": "failure", **failure})
                    print(f"request {index + 1} failed; new dispatch stopping; draining active requests.", flush=True)

        with ThreadPoolExecutor(max_workers=48) as pool:
            futures = []
            for ordinal, index in enumerate(p["pending_indices"]):
                if ordinal:
                    time.sleep(INTERVAL)
                if stop.is_set():
                    break
                with lock:
                    event({"event": "submitted", "index": index})
                dispatched.append(index)
                futures.append(pool.submit(worker, index))
            for future in futures:
                future.result()
        complete = not failures and set(records) == set(range(48))
        result = {"kind": p["kind"], "complete": complete, "unscored_model_responses": True,
                  "created_utc": base.now(), "plan_file_sha256": plan_sha,
                  "evidence_scope": p["evidence_scope"], "records": [records[i] for i in sorted(records)],
                  "preserved_indices": p["preserved_indices"], "new_dispatched_indices": dispatched,
                  "interrupted_prior_indices": p["interrupted_prior_indices"],
                  "known_physical_attempts": p["prior_physical_attempts"] + len(dispatched),
                  "total_usage_complete": False, "failures": failures,
                  "known_response_usage": {k: sum(r["response"][k] for r in records.values()) for k in ("input_tokens", "output_tokens")}}
        base.save(OUT / ("generation.json" if complete else "failure.json"), result)
        event({"event": "complete" if complete else "incomplete", "saved_count": len(records)})
        print(f"Migration finished: complete={complete}, saved={len(records)}/48; ambiguous prior usage excluded.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    plan() if args.command == "plan" else run(args.execute)
