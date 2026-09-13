"""48-task GLM exploratory replication using the passed streaming transport."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import threading
import time

HERE = Path(__file__).resolve()
spec = importlib.util.spec_from_file_location("stream", HERE.with_name("glm53-stream-canary-20260909.py"))
stream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stream)
base = stream.base
OUT = base.ROOT / "artifacts/glm53-stream-live-20260909"


def plan():
    stream.checked_plan()
    canary = base.read(stream.OUT / "canary.json")
    if (canary["passed"] is not True or not base.canary_pass(canary["records"])
        or canary["plan_file_sha256"] != base.sha(stream.OUT / "plan.json")):
        raise ValueError("requires passed matching stream canary")
    original = stream.glm.checked_plan()
    p = {"kind": "glm53_streaming_exploratory_followup", "created_utc": base.now(),
         "settings": stream.SETTINGS, "endpoint": stream.glm.ENDPOINT,
         "tasks": original["tasks"], "benchmark_binding": original["benchmark_binding"],
         "dispatch_interval_seconds": 3, "max_workers": 48, "maximum_calls": 48,
         "maximum_completion_tokens": 48 * 131072,
         "hard_total_timeout_seconds": stream.TOTAL_TIMEOUT,
         "no_progress_timeout_seconds": stream.NO_PROGRESS_TIMEOUT,
         "source_file_sha256": base.sha(HERE), "stream_source_file_sha256": base.sha(stream.HERE),
         "canary_plan_file_sha256": base.sha(stream.OUT / "plan.json"),
         "canary_file_sha256": base.sha(stream.OUT / "canary.json"),
         "automatic_retry": False, "automatic_resume": False, "private_key_read": False,
         "evidence_scope": original["evidence_scope"], "comparison": original["comparison"],
         "failure_policy": "stop submitting after transport/envelope failure, drain submitted requests; no retry; invalid final option consumes slot and paired world tie/miss",
         "caveats": original["caveats"] + "; GLM streaming versus DeepSeek nonstreaming; technical probes excluded; same 24 development worlds"}
    OUT.mkdir(parents=True, exist_ok=True, mode=0o700)
    base.save(OUT / "plan.json", p)
    print("GLM stream live plan saved: 48 calls, no repeat canary.", flush=True)


def checked_plan():
    stream.checked_plan()
    original = stream.glm.checked_plan()
    p = base.read(OUT / "plan.json")
    if (p["source_file_sha256"] != base.sha(HERE) or p["stream_source_file_sha256"] != base.sha(stream.HERE)
        or p["canary_plan_file_sha256"] != base.sha(stream.OUT / "plan.json")
        or p["canary_file_sha256"] != base.sha(stream.OUT / "canary.json")
        or p["settings"] != stream.SETTINGS or p["tasks"] != original["tasks"]
        or p["benchmark_binding"] != original["benchmark_binding"]
        or p["maximum_calls"] != 48 or p["dispatch_interval_seconds"] != 3 or p["max_workers"] != 48
        or p["hard_total_timeout_seconds"] != stream.TOTAL_TIMEOUT
        or p["no_progress_timeout_seconds"] != stream.NO_PROGRESS_TIMEOUT):
        raise ValueError("GLM streaming live plan drift")
    return p


def run(execute):
    if not execute: raise ValueError("requires --execute")
    p = checked_plan()
    key = stream.glm.read_key()
    fd = os.open(OUT / "attempts.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    lock = threading.Lock()
    stop = threading.Event()
    with os.fdopen(fd, "w") as ledger:
        def event(value):
            with lock:
                ledger.write(json.dumps({"utc": base.now(), **value}) + "\n")
                ledger.flush(); os.fsync(ledger.fileno())
                if value["event"] in ("first_sse", "response", "failure"):
                    print(json.dumps(value), flush=True)
        plan_sha = base.sha(OUT / "plan.json")
        event({"event": "start", "plan_file_sha256": plan_sha})
        def worker(i):
            event({"event": "attempt_started", "index": i, "task_id": p["tasks"][i]["task_id"]})
            try:
                result = stream.request(p["tasks"][i], key, lambda data: event({"index": i, **data}))
            except Exception as exc:
                result = {"record": None, "failure": type(exc).__name__, "telemetry": {"reported_usage": None}}
            if result["failure"]: stop.set()
            base.save(OUT / f"response-{i:02d}.json", result)
            event({"event": "failure" if result["failure"] else "response", "index": i,
                   "failure": result["failure"], "telemetry": result["telemetry"]})
            return result
        futures = []
        with ThreadPoolExecutor(max_workers=48) as pool:
            for i in range(48):
                if i: time.sleep(3)
                if stop.is_set(): break
                event({"event": "submitted", "index": i})
                futures.append(pool.submit(worker, i))
            results = [f.result() for f in futures]
        records = [r["record"] for r in results if r["record"] is not None]
        complete = len(results) == 48 and all(r["failure"] is None for r in results) and len(records) == 48
        summary = {"kind": p["kind"], "complete": complete, "results": results, "records": records,
                   "plan_file_sha256": plan_sha, "provider_calls": len(futures), "evidence_scope": p["evidence_scope"],
                   "unscored_model_responses": True, "usage_complete": complete,
                   "known_response_usage": {k: sum(r["response"][k] for r in records) for k in ("input_tokens", "output_tokens")},
                   "created_utc": base.now()}
        base.save(OUT / ("generation.json" if complete else "failure.json"), summary)
        event({"event": "complete" if complete else "incomplete", "provider_calls": len(futures), "saved_count": len(records)})
        print(f"GLM stream follow-up finished: complete={complete}, saved={len(records)}/48; ready for offline review.", flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'run'))
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    plan() if args.command == 'plan' else run(args.execute)
