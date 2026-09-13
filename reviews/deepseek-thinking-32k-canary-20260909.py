"""Separate 32k technical retry configuration; exactly two calls, no benchmark."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import time

HERE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("thinking_base", HERE.with_name("deepseek-thinking-followup-20260909.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
OUT = base.ROOT / "artifacts/deepseek-thinking-32k-canary-20260909"
SETTINGS = {**base.SETTINGS, "max_tokens": 32768}
TIMEOUT = 900


def plan():
    old, _ = base.checked_plan()
    result = {"kind": "thinking_32k_technical_canary", "created_utc": base.now(),
              "settings": SETTINGS, "timeout_seconds": TIMEOUT, "endpoint": base.ENDPOINT,
              "tasks": old["canary_tasks"], "maximum_calls": 2, "benchmark_calls": 0,
              "maximum_completion_tokens": 65536, "retry": False, "resume": False,
              "evidence": False, "private_key_read": False,
              "scope": "post-truncation budget calibration only; no ability scoring or exchangeability proof",
              "source_file_sha256": base.sha(HERE),
              "previous_plan_file_sha256": base.sha(base.OUT / "plan.json"),
              "previous_canary_file_sha256": base.sha(base.OUT / "canary.json"),
              "failure_policy": "stop on transport/envelope error; preserve invalid responses; never launch benchmark",
              "pass_rule": old["canary_pass"]}
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.save(OUT / "plan.json", result)
    print("32k plan saved: exactly two technical calls, no benchmark.", flush=True)


def checked_plan():
    old, _ = base.checked_plan()
    p = base.read(OUT / "plan.json")
    if (p["source_file_sha256"] != base.sha(HERE) or p["settings"] != SETTINGS
        or p["timeout_seconds"] != TIMEOUT or p["tasks"] != old["canary_tasks"]
        or p["endpoint"] != base.ENDPOINT or p["maximum_calls"] != 2 or p["benchmark_calls"] != 0
        or p["previous_plan_file_sha256"] != base.sha(base.OUT / "plan.json")
        or p["previous_canary_file_sha256"] != base.sha(base.OUT / "canary.json")):
        raise ValueError("32k plan or bound inputs changed")
    return p


def request_body(task):
    return {**SETTINGS, "messages": [{"role": "user", "content": task["rendered_prompt"]}]}


def run(execute):
    if not execute:
        raise ValueError("requires --execute")
    p = checked_plan()
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("DEEPSEEK_API_KEY absent")
    fd = os.open(OUT / "attempts.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as ledger:
        def event(value):
            ledger.write(json.dumps({"utc": base.now(), **value}) + "\n")
            ledger.flush()
            os.fsync(ledger.fileno())
        plan_sha = base.sha(OUT / "plan.json")
        event({"event": "start", "plan_file_sha256": plan_sha})
        records = []
        transport = base.UrllibHTTPTransport()
        for index, task in enumerate(p["tasks"]):
            event({"event": "attempt_started", "index": index, "task_id": task["task_id"]})
            start = time.monotonic()
            try:
                response = transport.post(url=base.ENDPOINT,
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                    body=json.dumps(request_body(task)).encode(), timeout=TIMEOUT)
                if response.status != 200:
                    raise base.HTTPStatusError(response.status)
                record = base.record_response(task, json.loads(response.body), (time.monotonic() - start) * 1000)
            except Exception as exc:
                failure = {"complete": False, "evidence": False, "benchmark_calls": 0,
                           "plan_file_sha256": plan_sha, "index": index, "records": records,
                           "error_type": type(exc).__name__, "http_status": getattr(exc, "status_code", None),
                           "transport_category": getattr(exc, "category", None), "usage_complete": False}
                event({"event": "failure", **{k: v for k, v in failure.items() if k != "records"}})
                base.save(OUT / "failure.json", failure)
                print("Stopped; no retry: " + type(exc).__name__, flush=True)
                return
            records.append(record)
            event({"event": "response", "index": index, "record": record})
            print(f"canary {index + 1}/2 saved; valid={record['valid_choice']}; finish={record['response']['finish_reason']}; output_tokens={record['response']['output_tokens']}", flush=True)
        result = {"kind": p["kind"], "complete": True, "passed": base.canary_pass(records),
                  "created_utc": base.now(), "plan_file_sha256": plan_sha, "records": records,
                  "evidence": False, "benchmark_calls": 0, "provider_calls": 2,
                  "usage": {k: sum(r["response"][k] for r in records) for k in ("input_tokens", "output_tokens")}}
        base.save(OUT / "canary.json", result)
        event({"event": "complete", "passed": result["passed"], "calls": 2})
        print(f"Finished; passed={result['passed']}; benchmark calls=0.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    plan() if args.command == "plan" else run(args.execute)
